#!/usr/bin/env python3
"""Development-only helper for mapping renderer anchors across ASAR builds.

Read-only: it never modifies the official app bundle. It operates on an
extracted ASAR directory (see docs/INTEL-RENDERER-8378.md)::

    python3 scripts/analyze_asar.py chunks /tmp/chatgpt-8378-analysis/asar
    python3 scripts/analyze_asar.py find /tmp/chatgpt-8378-analysis/asar \\
        --term "rate-limit-reset-credits" --context 200
    python3 scripts/analyze_asar.py counts /tmp/chatgpt-8378-analysis/asar \\
        --term-file /tmp/terms.txt --include "webview/assets/app-initial-*.js"
    python3 scripts/analyze_asar.py diff-manifest old/MANIFEST.tsv new/MANIFEST.tsv

Large proprietary chunks are never printed in full; only bounded context
windows around matches are shown.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path


TEXT_SUFFIXES = {".js", ".cjs", ".mjs", ".html", ".json"}


def iter_text_files(root: Path, include: str | None):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        relative = str(path.relative_to(root))
        if include and not fnmatch.fnmatch(relative, include):
            continue
        yield path


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def cmd_chunks(args: argparse.Namespace) -> int:
    root = Path(args.extracted)
    for path in iter_text_files(root, args.include):
        if path.suffix in (".js", ".cjs", ".mjs"):
            print(f"{path.stat().st_size:>10}  {path.relative_to(root)}")
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    root = Path(args.extracted)
    total = 0
    for path in iter_text_files(root, args.include):
        text = read_text(path)
        if text is None:
            continue
        start = 0
        shown = 0
        while True:
            index = text.find(args.term, start)
            if index < 0:
                break
            total += 1
            if shown < args.max_shown:
                before = max(0, index - args.context)
                after = index + len(args.term) + args.context
                snippet = text[before:after].replace("\n", "\\n")
                print(f"--- {path.relative_to(root)} @ {index}")
                print(snippet)
                shown += 1
            start = index + len(args.term)
        if shown and total > shown and shown == args.max_shown:
            pass
    print(f"total occurrences of {args.term!r}: {total}")
    return 0


def cmd_counts(args: argparse.Namespace) -> int:
    root = Path(args.extracted)
    terms = list(args.term or [])
    if args.term_file:
        terms.extend(
            line.strip()
            for line in Path(args.term_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    files = list(iter_text_files(root, args.include))
    texts: dict[str, str] = {}
    for path in files:
        text = read_text(path)
        if text is not None:
            texts[str(path.relative_to(root))] = text
    for term in terms:
        per_file = [
            (name, text.count(term)) for name, text in texts.items() if term in text
        ]
        total = sum(count for _, count in per_file)
        print(f"{term!r}: total={total}")
        for name, count in per_file[:10]:
            print(f"    {count:>4}x  {name}")
    return 0


def cmd_diff_manifest(args: argparse.Namespace) -> int:
    def load(path: Path) -> dict[str, tuple[str, str]]:
        entries: dict[str, tuple[str, str]] = {}
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            if not line.strip():
                continue
            name, size, digest = line.split("\t")
            entries[name] = (size, digest)
        return entries

    old = load(Path(args.old))
    new = load(Path(args.new))
    for name in sorted(set(old) | set(new)):
        if name not in old:
            print(f"ADDED    {name}")
        elif name not in new:
            print(f"REMOVED  {name}")
        elif old[name] != new[name]:
            print(f"CHANGED  {name} ({old[name][0]} -> {new[name][0]} bytes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    chunks = sub.add_parser("chunks", help="list JS chunks with sizes")
    chunks.add_argument("extracted", type=Path)
    chunks.add_argument("--include", default=None)
    chunks.set_defaults(handler=cmd_chunks)

    find = sub.add_parser("find", help="show bounded context around a literal")
    find.add_argument("extracted", type=Path)
    find.add_argument("--term", required=True)
    find.add_argument("--context", type=int, default=250)
    find.add_argument("--include", default=None)
    find.add_argument("--max-shown", type=int, default=5)
    find.set_defaults(handler=cmd_find)

    counts = sub.add_parser("counts", help="count literals per file")
    counts.add_argument("extracted", type=Path)
    counts.add_argument("--term", action="append", default=[])
    counts.add_argument("--term-file", default=None)
    counts.add_argument("--include", default=None)
    counts.set_defaults(handler=cmd_counts)

    diff = sub.add_parser("diff-manifest", help="compare two MANIFEST.tsv files")
    diff.add_argument("old")
    diff.add_argument("new")
    diff.set_defaults(handler=cmd_diff_manifest)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
