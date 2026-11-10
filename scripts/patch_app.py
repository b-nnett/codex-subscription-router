#!/usr/bin/env python3
"""Create an independently signed ChatGPT.app copy with Codex multiplexing."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
DEFAULT_SOURCE = Path("/Applications/ChatGPT.app")
DEFAULT_DESTINATION = Path.home() / "Applications" / "Codex Subscription Router.app"
DEFAULT_STATE_ROOT = Path.home() / ".codex-mux"
CONTROL_PORT = 48123
DESKTOP_PROFILE_NAME = "Codex Subscription Router"
DESKTOP_BUNDLE_IDENTIFIER = "app.cdxmux.multi"
OPENAI_DESKTOP_CODE_IDENTIFIER = "com.openai.codex"
OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER = "com.openai.sky.CUAService"
COMPUTER_USE_BUNDLE_IDENTIFIER = "com.cdxmux.sky.CUAService"
COMPUTER_USE_DISPLAY_NAME = "Codex Subscription Router Computer Use"
COMPUTER_USE_APP_NAME = f"{COMPUTER_USE_DISPLAY_NAME}.app"
LAUNCH_SERVICES_REGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)
ASAR_UNPACK_DIRECTORIES = (
    "node_modules/{@worklouder,better-sqlite3,node-mac-permissions,node-pty,objc-js}"
)
PREFERRED_SIGNING_IDENTITY_PREFIXES = (
    "Developer ID Application:",
    "Apple Development:",
)
OPENAI_INTERNAL_TEAM_IDENTIFIER = "HX7739G8FX"
OPENAI_DISTRIBUTION_TEAM_IDENTIFIER = "2DC432GLL2"
TESTED_SOURCE_BUILDS = {
    (
        "26.803.61601",
        "6396",
    ): "d5a44ed9e2f1db5f81dbbe85408aed256f3203c5b16f00817bb9d7cd941343cf",
    (
        "26.1103.60933",
        "25894",
    ): "a77588b2dcfa9b394d722dd8c2c8b13d3a09bacd49c839cc715245742dba6048",
}
# Recorded per release: the desktop shell embeds a different number of Computer
# Use service references each time [OI] rebuilds the renderer.
ASAR_CUA_IDENTIFIER_REPLACEMENTS = {
    ("26.803.61601", "6396"): 17,
    ("26.1103.60933", "25894"): 8,
}
EXPECTED_CUA_IDENTIFIER_REPLACEMENTS = 49


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {PROJECT_VERSION}")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing destination after moving it to a timestamped backup.",
    )
    parser.add_argument(
        "--allow-adhoc-signing",
        action="store_true",
        help="Allow an ad-hoc signature (Appshots and Computer Use may stop working).",
    )
    parser.add_argument(
        "--allow-untested-source",
        action="store_true",
        help="Continue after an explicit version, build, or ASAR hash mismatch.",
    )
    parser.add_argument(
        "--allow-signing-team-change",
        action="store_true",
        help="Replace an existing build signed by a different Apple team.",
    )
    return parser.parse_args()


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def output(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"required tool not found: {name}")


def resolve_signing_identity(allow_adhoc: bool) -> str:
    configured = os.environ.get("CODEX_MUX_SIGNING_IDENTITY", "").strip()
    if configured:
        return configured
    identities = output(["security", "find-identity", "-v", "-p", "codesigning"])
    available = re.findall(
        r'^\s*\d+\)\s+[0-9A-F]+\s+"([^"]+)"',
        identities,
        re.MULTILINE,
    )
    for prefix in PREFERRED_SIGNING_IDENTITY_PREFIXES:
        for identity in available:
            if identity.startswith(prefix):
                return identity
    if allow_adhoc:
        print(
            "Warning: using an ad-hoc signature; Appshots and Computer Use may be unavailable.",
            file=sys.stderr,
        )
        return "-"
    raise RuntimeError(
        "no team-backed code-signing identity found; set CODEX_MUX_SIGNING_IDENTITY "
        "or explicitly pass --allow-adhoc-signing"
    )


def signing_team_identifier(identity: str) -> str | None:
    if identity == "-":
        return None
    match = re.search(r"\(([A-Z0-9]{10})\)$", identity)
    if match is None:
        raise RuntimeError(
            "the signing identity must end with its 10-character Apple team ID"
        )
    return match.group(1)


def signed_code_metadata(path: Path) -> tuple[str | None, str | None]:
    result = subprocess.run(
        ["codesign", "--display", "--verbose=4", str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    details = result.stdout + result.stderr
    identifier_match = re.search(r"^Identifier=(.+)$", details, re.MULTILINE)
    team_match = re.search(r"^TeamIdentifier=(.+)$", details, re.MULTILINE)
    identifier = identifier_match.group(1).strip() if identifier_match else None
    team = team_match.group(1).strip() if team_match else None
    if team == "not set":
        team = None
    return identifier, team


def verify_signed_code(
    path: Path,
    expected_identifier: str,
    expected_team: str | None,
) -> None:
    run(["codesign", "--verify", "--deep", "--strict", str(path)])
    identifier, team = signed_code_metadata(path)
    if identifier != expected_identifier:
        raise RuntimeError(
            f"unexpected signing identifier on {path}: {identifier!r}"
        )
    if team != expected_team:
        raise RuntimeError(f"unexpected signing team on {path}: {team!r}")


def existing_signing_team(path: Path) -> str | None:
    if not path.exists():
        return None
    plist_path = path / "Contents" / "Info.plist"
    if plist_path.is_file():
        try:
            with plist_path.open("rb") as handle:
                recorded = plistlib.load(handle).get("CodexMuxSigningTeamIdentifier")
            if isinstance(recorded, str) and recorded != "":
                return None if recorded == "adhoc" else recorded
        except (OSError, plistlib.InvalidFileException):
            pass
    _, team = signed_code_metadata(path)
    return team


def ensure_components_are_stopped(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if not path.exists():
            continue
        result = subprocess.run(
            ["pgrep", "-f", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            raise RuntimeError(
                f"quit the running component before replacing it: {path}"
            )


MACH_O_MAGICS = {
    b"\xfe\xed\xfa\xce",  # 32-bit, big endian
    b"\xfe\xed\xfa\xcf",  # 64-bit, big endian
    b"\xce\xfa\xed\xfe",  # 32-bit, little endian
    b"\xcf\xfa\xed\xfe",  # 64-bit, little endian
    b"\xca\xfe\xba\xbe",  # universal binary
    b"\xbe\xba\xfe\xca",  # universal binary, little endian
}


def is_mach_o(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(4) in MACH_O_MAGICS
    except OSError:
        return False


def arm64_swift_small_string(value: str) -> bytes:
    """Encode the instructions used to materialize a 10-byte Swift string."""
    encoded = value.encode("ascii")
    if len(encoded) != 10:
        raise ValueError("a signing team identifier must contain 10 ASCII bytes")

    def instruction(base: int, immediate: int, register: int, shift: int = 0) -> bytes:
        word = base | ((shift // 16) << 21) | (immediate << 5) | register
        return word.to_bytes(4, "little")

    chunks = [
        int.from_bytes(encoded[index : index + 2], "little")
        for index in range(0, len(encoded), 2)
    ]
    return b"".join(
        (
            instruction(0xD2800000, chunks[0], 0),
            instruction(0xF2800000, chunks[1], 0, 16),
            instruction(0xF2800000, chunks[2], 0, 32),
            instruction(0xF2800000, chunks[3], 0, 48),
            instruction(0xD2800000, chunks[4], 1),
            instruction(0xF2800000, 0xEA00, 1, 48),
        )
    )


def replace_same_length_identifier(
    path: Path, original: str, replacement: str
) -> int:
    """Replace an embedded identifier without changing binary or bundle offsets."""
    original_bytes = original.encode("ascii")
    replacement_bytes = replacement.encode("ascii")
    if len(original_bytes) != len(replacement_bytes):
        raise RuntimeError("replacement identifiers must have the same byte length")
    data = path.read_bytes()
    count = data.count(original_bytes)
    if count:
        path.write_bytes(data.replace(original_bytes, replacement_bytes))
    return count


def computer_use_package(app: Path) -> Path:
    return (
        app
        / "Contents"
        / "Resources"
        / "cua_node"
        / "lib"
        / "node_modules"
        / "@oai"
        / "sky"
    )


def retire_stale_cached_computer_use_app() -> None:
    """Move aside only a prior custom helper copied into the shared Codex home."""
    cached_app = (
        Path.home() / ".codex" / "computer-use" / "Codex Computer Use.app"
    )
    plist_path = cached_app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return
    try:
        with plist_path.open("rb") as handle:
            bundle_identifier = plistlib.load(handle).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException):
        return
    if bundle_identifier != COMPUTER_USE_BUNDLE_IDENTIFIER:
        return
    if LAUNCH_SERVICES_REGISTER.is_file():
        run([str(LAUNCH_SERVICES_REGISTER), "-u", str(cached_app)])
    backup = cached_app.with_name(
        f"Codex Computer Use backup-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    cached_app.rename(backup)
    print(f"Stale cached Computer Use helper moved to {backup}")


def patch_computer_use_identity(app: Path, team_identifier: str | None) -> None:
    """Give the copied CUA service an independent identity and trusted callers."""
    package = computer_use_package(app)
    service = package / "Codex Computer Use.app"
    executable = service / "Contents" / "MacOS" / "SkyComputerUseService"
    if not executable.is_file():
        raise RuntimeError("bundled Codex Computer Use service was not found")

    for profile in package.rglob("embedded.provisionprofile"):
        profile.unlink()

    identifier_replacements = 0
    for candidate in package.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            identifier_replacements += replace_same_length_identifier(
                candidate,
                OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER,
                COMPUTER_USE_BUNDLE_IDENTIFIER,
            )
    if identifier_replacements != EXPECTED_CUA_IDENTIFIER_REPLACEMENTS:
        raise RuntimeError(
            "expected "
            f"{EXPECTED_CUA_IDENTIFIER_REPLACEMENTS} Computer Use identity "
            f"references, found {identifier_replacements}"
        )

    plist_path = service / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleIdentifier"] = COMPUTER_USE_BUNDLE_IDENTIFIER
    info["CFBundleDisplayName"] = COMPUTER_USE_DISPLAY_NAME
    info["CFBundleName"] = COMPUTER_USE_DISPLAY_NAME
    for key in list(info):
        if key.startswith("SU"):
            del info[key]
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)

    if team_identifier is None:
        return
    binary = executable.read_bytes()
    replacement = arm64_swift_small_string(team_identifier)
    for original_team, description in (
        (OPENAI_INTERNAL_TEAM_IDENTIFIER, "internal"),
        (OPENAI_DISTRIBUTION_TEAM_IDENTIFIER, "distribution"),
    ):
        original = arm64_swift_small_string(original_team)
        match_count = binary.count(original)
        if match_count != 2:
            raise RuntimeError(
                f"expected two Computer Use {description}-team checks, "
                f"found {match_count}; the official app layout may have changed"
            )
        binary = binary.replace(original, replacement)

        raw_original = original_team.encode("ascii")
        raw_replacement = team_identifier.encode("ascii")
        raw_match_count = binary.count(raw_original)
        expected_raw_matches = 1 if description == "internal" else 17
        if raw_match_count != expected_raw_matches:
            raise RuntimeError(
                f"expected {expected_raw_matches} Computer Use {description}-team "
                f"constants, found {raw_match_count}; the official app layout may have changed"
            )
        binary = binary.replace(raw_original, raw_replacement)

    original_bundle_id = b"com.openai.codex\0"
    replacement_bundle_id = DESKTOP_BUNDLE_IDENTIFIER.encode("ascii") + b"\0"
    if len(replacement_bundle_id) != len(original_bundle_id):
        raise RuntimeError(
            "the independent bundle identifier must match the CUA identifier length"
        )
    if binary.count(original_bundle_id) != 1:
        raise RuntimeError("could not find the Computer Use production bundle ID")
    executable.write_bytes(binary.replace(original_bundle_id, replacement_bundle_id))


def patch_asar_computer_use_identity(
    extracted: Path, expected_replacements: int | None
) -> None:
    """Keep desktop launch, temp-file, and service references on the new CUA ID."""
    replacements = 0
    for candidate in extracted.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            replacements += replace_same_length_identifier(
                candidate,
                OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER,
                COMPUTER_USE_BUNDLE_IDENTIFIER,
            )
    if expected_replacements is None:
        print(
            "Warning: this ChatGPT build has no recorded Computer Use reference "
            f"count; continuing with {replacements} rewritten references.",
            file=sys.stderr,
        )
        return
    if replacements != expected_replacements:
        raise RuntimeError(
            f"expected {expected_replacements} Computer Use references "
            f"in app.asar, found {replacements}"
        )


def sign_native_code_tree(root: Path, identity: str) -> None:
    """Sign native modules before ASAR records their final sizes."""
    if not root.is_dir():
        return
    options = [] if ADHOC_SIGNING else ["--options", "runtime"]
    for candidate in root.rglob("*"):
        if not is_mach_o(candidate):
            continue
        run(
            [
                "codesign",
                "--force",
                "--sign",
                identity,
                "--timestamp=none",
                *options,
                str(candidate),
            ]
        )


TEAM_SCOPED_ENTITLEMENTS = (
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
    "com.apple.security.application-groups",
    "keychain-access-groups",
)
# Ad-hoc signatures cannot carry Apple-restricted entitlements: AMFI refuses to
# validate the binary ("adhoc signed but contains restricted entitlements").
RESTRICTED_ENTITLEMENT_PREFIXES = ("com.apple.developer.",)
ADHOC_SIGNING = False


def sanitized_runtime_entitlements(executable: Path) -> dict[str, object] | None:
    """Keep runtime capabilities while removing the official app's team grants."""
    result = subprocess.run(
        ["codesign", "--display", "--entitlements", ":-", str(executable)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if not result.stdout.strip():
        return None
    try:
        entitlements = plistlib.loads(result.stdout)
    except plistlib.InvalidFileException as error:
        raise RuntimeError(
            f"could not read signing entitlements from {executable}"
        ) from error
    if not isinstance(entitlements, dict):
        raise RuntimeError(f"invalid signing entitlements on {executable}")
    for key in TEAM_SCOPED_ENTITLEMENTS:
        entitlements.pop(key, None)
    if ADHOC_SIGNING:
        for key in list(entitlements):
            if key.startswith(RESTRICTED_ENTITLEMENT_PREFIXES):
                del entitlements[key]
    return entitlements or None


AUTO_ENTITLEMENTS = object()


def sign_runtime_executable(
    executable: Path,
    identity: str,
    identifier: str | None = None,
    entitlements: dict[str, object] | None | object = AUTO_ENTITLEMENTS,
    runtime: bool = True,
) -> None:
    """Re-sign an embedded runtime without breaking JIT-backed processes."""
    if entitlements is AUTO_ENTITLEMENTS:
        entitlements = sanitized_runtime_entitlements(executable)
    # Ad-hoc signatures carry no team, so the hardened runtime would enforce
    # library validation between components that can never share a team.
    if ADHOC_SIGNING:
        runtime = False
    command = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--timestamp=none",
    ]
    if runtime:
        command.extend(("--options", "runtime"))
    if identifier is None:
        command.append("--preserve-metadata=identifier")
    else:
        command.extend(("--identifier", identifier))
    if entitlements is None:
        run([*command, str(executable)])
        return
    with tempfile.TemporaryDirectory(prefix=".codesign-entitlements-") as temporary:
        entitlements_path = Path(temporary) / "entitlements.plist"
        with entitlements_path.open("wb") as handle:
            plistlib.dump(
                entitlements,
                handle,
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        run([*command, "--entitlements", str(entitlements_path), str(executable)])


def bundle_main_executable(bundle: Path) -> Path | None:
    plist_path = bundle / "Contents" / "Info.plist"
    executable_root = bundle / "Contents" / "MacOS"
    if bundle.suffix == ".framework":
        plist_path = bundle / "Versions" / "Current" / "Resources" / "Info.plist"
        executable_root = bundle / "Versions" / "Current"
    if not plist_path.is_file():
        return None
    with plist_path.open("rb") as handle:
        executable_name = plistlib.load(handle).get("CFBundleExecutable")
    if not isinstance(executable_name, str) or executable_name == "":
        return None
    executable = executable_root / executable_name
    return executable if executable.is_file() else None


def sign_runtime_bundle(
    bundle: Path,
    identity: str,
    identifier: str | None = None,
    entitlements: dict[str, object] | None | object = AUTO_ENTITLEMENTS,
    runtime: bool = True,
) -> None:
    if entitlements is AUTO_ENTITLEMENTS:
        executable = bundle_main_executable(bundle)
        entitlements = (
            sanitized_runtime_entitlements(executable)
            if executable is not None
            else None
        )
    if ADHOC_SIGNING:
        runtime = False
    command = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--timestamp=none",
    ]
    if runtime:
        command.extend(("--options", "runtime"))
    if identifier is not None:
        command.extend(("--identifier", identifier))
    if entitlements is None:
        run([*command, str(bundle)])
        return
    with tempfile.TemporaryDirectory(prefix=".codesign-entitlements-") as temporary:
        entitlements_path = Path(temporary) / "entitlements.plist"
        with entitlements_path.open("wb") as handle:
            plistlib.dump(
                entitlements,
                handle,
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        run([*command, "--entitlements", str(entitlements_path), str(bundle)])


def capture_computer_use_entitlements(
    app: Path,
) -> dict[Path, dict[str, object] | None]:
    service = computer_use_package(app) / "Codex Computer Use.app"
    if not service.is_dir():
        raise RuntimeError("bundled Codex Computer Use service was not found")
    return {
        executable.relative_to(service): sanitized_runtime_entitlements(executable)
        for executable in service.rglob("*")
        if is_mach_o(executable)
    }


def sign_computer_use_code(
    app: Path,
    identity: str,
    preserved_entitlements: dict[Path, dict[str, object] | None],
) -> None:
    """Keep the Computer Use service and its callers on one signing team."""
    resources = app / "Contents" / "Resources"
    service = computer_use_package(app) / "Codex Computer Use.app"
    if not service.is_dir():
        raise RuntimeError("bundled Codex Computer Use service was not found")

    for executable in sorted(
        (candidate for candidate in service.rglob("*") if is_mach_o(candidate)),
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    ):
        relative = executable.relative_to(service)
        sign_runtime_executable(
            executable,
            identity,
            entitlements=preserved_entitlements.get(relative),
        )

    bundle_suffixes = {".app", ".appex", ".bundle", ".framework", ".xpc"}
    bundles = [
        candidate
        for candidate in service.rglob("*")
        if candidate.is_dir() and candidate.suffix in bundle_suffixes
    ]
    bundles.append(service)
    for bundle in sorted(
        set(bundles),
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    ):
        identifier = (
            COMPUTER_USE_BUNDLE_IDENTIFIER if bundle == service else None
        )
        executable = bundle_main_executable(bundle)
        entitlements = (
            preserved_entitlements.get(executable.relative_to(service))
            if executable is not None
            else None
        )
        sign_runtime_bundle(bundle, identity, identifier, entitlements)
        run(["codesign", "--verify", "--deep", "--strict", str(bundle)])

    for executable_name in ("node", "node_repl"):
        executable = resources / "cua_node" / "bin" / executable_name
        sign_runtime_executable(executable, identity)
    sign_runtime_executable(
        app / "Contents" / "MacOS" / "ChatGPT",
        identity,
        OPENAI_DESKTOP_CODE_IDENTIFIER,
        runtime=False,
    )


def strip_provisioning_profiles(root: Path) -> None:
    """Ad-hoc copies cannot keep the official app's provisioning profile grants."""
    if not root.is_dir():
        return
    for profile in root.rglob("embedded.provisionprofile"):
        profile.unlink()


def sign_embedded_code_tree(root: Path, identity: str) -> None:
    """Re-sign vendored executables so a deep verify of the copy succeeds."""
    if not root.is_dir():
        return
    # Mach-O files first, then the bundles that seal them, deepest first.
    for executable in sorted(
        (candidate for candidate in root.rglob("*") if is_mach_o(candidate)),
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    ):
        sign_runtime_executable(executable, identity)
    bundle_suffixes = {".app", ".appex", ".bundle", ".framework", ".xpc"}
    bundles = [
        candidate
        for candidate in root.rglob("*")
        if candidate.is_dir() and candidate.suffix in bundle_suffixes
    ]
    for bundle in sorted(
        set(bundles), key=lambda candidate: len(candidate.parts), reverse=True
    ):
        sign_runtime_bundle(bundle, identity)


def sign_independent_app(
    app: Path, identity: str, team_identifier: str | None
) -> None:
    """Apply one stable identity throughout the modified Electron bundle."""
    computer_use_entitlements = capture_computer_use_entitlements(app)
    patch_computer_use_identity(app, team_identifier)
    sign_computer_use_code(app, identity, computer_use_entitlements)
    resources = app / "Contents" / "Resources"
    strip_provisioning_profiles(resources / "codex-cli")
    sign_embedded_code_tree(resources / "codex-cli", identity)
    sign_embedded_code_tree(resources / "native", identity)
    # The integrity digest slot was rewritten in place, so the framework that
    # carries it needs a fresh signature before the app is sealed.
    sign_embedded_code_tree(app / "Contents" / "Frameworks", identity)
    run(
        [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp=none",
            str(codex_cli_entrypoint(resources)),
        ]
    )
    run(
        [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp=none",
            str(app),
        ]
    )


def load_or_create_token() -> str:
    DEFAULT_STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    DEFAULT_STATE_ROOT.chmod(0o700)
    token_path = DEFAULT_STATE_ROOT / "control-token"
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise RuntimeError(f"invalid control token at {token_path}")
        token_path.chmod(0o600)
        return token
    token = secrets.token_hex(32)
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def codex_cli_supports_proxy(entrypoint: Path) -> bool:
    """Only the flat Resources/codex layout is safe to replace with the proxy."""
    return entrypoint.parent.name != "MacOS"


def codex_cli_entrypoint(resources: Path) -> Path:
    """Locate the Codex CLI the desktop shell launches for app-server sessions."""
    # The desktop shell execs the CLI bundle's own executable; bin/codex is only
    # a shim that forwards to it, so the proxy has to replace the real one.
    candidates = (
        resources / "codex-cli" / "CodexCLI.app" / "Contents" / "MacOS" / "codex",
        resources / "codex",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("bundled Codex CLI was not found")


def build_proxy(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "go",
            "build",
            "-trimpath",
            "-ldflags=-s -w",
            "-o",
            str(destination),
            "./cmd/codex-mux",
        ],
        cwd=PROJECT_ROOT,
    )
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_launcher(app: Path) -> None:
    """Pass Chromium its isolated profile before Electron's main process starts."""
    launcher = app / "Contents" / "MacOS" / "CodexSubscriptionRouterLauncher"
    run(
        [
            "xcrun",
            "clang",
            "-Os",
            "-Wall",
            "-Wextra",
            "-o",
            str(launcher),
            str(PROJECT_ROOT / "native" / "launcher.c"),
        ]
    )


def ensure_asar_tool() -> Path:
    asar = PROJECT_ROOT / "node_modules" / ".bin" / "asar"
    package_manifest = PROJECT_ROOT / "node_modules" / "@electron" / "asar" / "package.json"
    expected = json.loads(
        (PROJECT_ROOT / "package.json").read_text(encoding="utf-8")
    )["devDependencies"]["@electron/asar"]
    if not asar.exists() or not package_manifest.is_file():
        raise RuntimeError("run `npm ci --ignore-scripts` before patching")
    actual = json.loads(package_manifest.read_text(encoding="utf-8")).get("version")
    if actual != expected:
        raise RuntimeError(
            f"installed @electron/asar is {actual!r}, expected {expected!r}; "
            "run `npm ci --ignore-scripts`"
        )
    return asar


def renderer_asset_files(extracted: Path) -> list[Path]:
    return sorted((extracted / "webview" / "assets").rglob("*.js"))


def build_asset_files(extracted: Path) -> list[Path]:
    return sorted((extracted / ".vite" / "build").glob("*.js"))


def find_asset(
    files: list[Path], needle: bytes, description: str, *, required: bool = True
) -> Path | None:
    """Locate the single bundle that still carries a patch anchor."""
    matches = [path for path in files if path.is_file() and needle in path.read_bytes()]
    if len(matches) == 1:
        return matches[0]
    if required:
        raise RuntimeError(
            f"expected one {description} in this ChatGPT build, found {len(matches)}"
        )
    print(
        f"Warning: skipped {description}; the anchor is no longer present",
        file=sys.stderr,
    )
    return None


MODULE_START = re.compile(r"[,{]([A-Za-z0-9_$]+)\(e,t,n\)\{")


def module_region(source: str, position: int) -> str:
    """Return the rspack module body that contains position."""
    start = 0
    for match in MODULE_START.finditer(source):
        if match.start() > position:
            return source[start : match.start()]
        start = match.start()
    return source[start:]


def single_match(source: str, pattern: str, description: str) -> re.Match[str]:
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {description}, found {len(matches)}")
    return matches[0]


def detect_alias(region: str, pattern: str, description: str) -> str:
    counts = collections.Counter(re.findall(pattern, region))
    if not counts:
        raise RuntimeError(f"could not detect the {description} alias")
    return counts.most_common(1)[0][0]


def unique_identifier(region: str, base_name: str) -> str:
    """Pick a binding name that cannot shadow a local of the host module."""
    name = base_name
    suffix = 0
    while re.search(rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])", region):
        suffix += 1
        name = f"{base_name}{suffix}"
    return name


def host_bindings(region: str) -> tuple[dict[str, str], str, str]:
    """Build the alias preamble the injected menu needs in a ChatGPT module.

    ChatGPT's renderer is minified, so the local names for the JSX runtime, the
    React namespace, and the menu kit change with every build. Detect them from
    the module that renders the profile dropdown, and bind the names the injected
    component expects to freshly named locals so nothing is shadowed.
    """
    jsx_source = detect_alias(region, r"\(0,([A-Za-z0-9_$]+)\.jsx\)", "JSX runtime")
    react_source = detect_alias(
        region,
        r"\(0,([A-Za-z0-9_$]+)\.(?:useState|useCallback|useEffect|useMemo|useRef|useId)\b",
        "React runtime",
    )
    menu_source = detect_alias(region, r"([A-Za-z0-9_$]+)\.ms\.Separator", "menu kit")
    if f"{menu_source}.tJ" not in region or f"{jsx_source}.Fragment" not in region:
        raise RuntimeError("the ChatGPT menu primitives no longer match this patch")

    jsx = unique_identifier(region, "__codexMuxJsx")
    react = unique_identifier(region, "__codexMuxReact")
    menu = unique_identifier(region, "__codexMuxMenu")
    item = unique_identifier(region, "__codexMuxMenuItem")
    icon = unique_identifier(region, "__codexMuxStatusIcon")
    image = unique_identifier(region, "__codexMuxImageUrl")
    boundary = unique_identifier(region, "__codexMuxBoundary")

    preamble = (
        f"const {jsx} = {{ jsx: {jsx_source}.jsx, jsxs: {jsx_source}.jsxs, "
        f"Fragment: {jsx_source}.Fragment }};\n"
        f"const {react} = {react_source};\n"
        f"const {menu} = {menu_source}.ms;\n"
        f"const {item} = {menu_source}.tJ;\n"
        f"const {icon} = function CodexMuxStatusIcon() {{\n"
        f'  return (0, {jsx}.jsx)("span", {{"aria-hidden": true, '
        'className: "icon-sm inline-block rounded-full bg-current opacity-60" });\n'
        "};\n"
        f"const {image} = function codexMuxImageUrl(value) {{\n"
        "  return value || null;\n"
        "};\n"
        f"class {boundary} extends {react}.Component {{\n"
        f"  constructor(props) {{ super(props); this.state = {{ failed: false }}; }}\n"
        f"  static getDerivedStateFromError() {{ return {{ failed: true }}; }}\n"
        "  componentDidCatch(error) {\n"
        "    try {\n"
        "      console.warn(\"Codex Subscription Router menu failed\", error);\n"
        "    } catch {}\n"
        "  }\n"
        "  render() {\n"
        f"    return this.state.failed ? null : (0, {jsx}.jsx)(\"span\", "
        "{ style: { display: \"contents\" }, children: this.props.children });\n"
        "  }\n"
        "}\n\n"
    )
    substitutions = {
        "e7": jsx,
        "kXc": react,
        "CH": menu,
        "_H": item,
        "S2": icon,
        "jLa": image,
    }
    return substitutions, preamble, boundary


def patch_renderer_csp(extracted: Path) -> None:
    index_path = extracted / "webview" / "index.html"
    index = index_path.read_text(encoding="utf-8")
    connect_anchor = next(
        (
            anchor
            for anchor in ("connect-src &#39;self&#39;", "connect-src 'self'")
            if anchor in index
        ),
        None,
    )
    if connect_anchor is None:
        raise RuntimeError("could not find the ChatGPT renderer CSP connect-src")
    index = index.replace(
        connect_anchor,
        f"{connect_anchor} http://127.0.0.1:{CONTROL_PORT}",
        1,
    )
    index_path.write_text(index, encoding="utf-8")


def patch_account_menu(extracted: Path, token: str) -> None:
    path = find_asset(
        renderer_asset_files(extracted),
        b"ProfileDropdownItems:()=>",
        "native profile dropdown items",
    )
    if path is None:
        return
    source = path.read_text(encoding="utf-8")
    if "CodexMuxAccountMenu(" in source:
        raise RuntimeError("this ChatGPT build already contains the Codex multiplexer menu")
    anchor = source.index("ProfileDropdownItems:()=>")
    region = module_region(source, anchor)
    component = single_match(
        region,
        r"ProfileDropdownItems:\(\)=>([A-Za-z0-9_$]+)",
        "profile dropdown items export",
    ).group(1)
    substitutions, preamble, boundary = host_bindings(region)

    component_source = (PROJECT_ROOT / "ui" / "account-menu.js").read_text(
        encoding="utf-8"
    )
    component_source = component_source.replace(
        "__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT)
    )
    component_source = component_source.replace("__CODEX_MUX_CONTROL_TOKEN__", token)
    for original, replacement in substitutions.items():
        component_source = re.sub(
            rf"(?<![A-Za-z0-9_$.]){re.escape(original)}(?![A-Za-z0-9_$])",
            replacement,
            component_source,
        )

    component_anchor = f"function {component}("
    if source.count(component_anchor) != 1:
        raise RuntimeError("could not find the native ChatGPT profile menu component")
    source = source.replace(
        component_anchor,
        preamble + component_source + "\n" + component_anchor,
        1,
    )

    # The same property name is also destructured by the component that renders
    # the menu; only the object literal passed to it may be rewritten.
    usage_pattern = re.compile(
        r"usageItems:([A-Za-z0-9_$]+),"
        r"(?=workspaceInviteItem:[A-Za-z0-9_$]+\}(?!\s*=))"
    )
    source, replacements = usage_pattern.subn(
        f"usageItems:(0,{substitutions['e7']}.jsx)({boundary},{{children:"
        f"(0,{substitutions['e7']}.jsx)(CodexMuxAccountMenu,{{}})}}),",
        source,
        count=1,
    )
    if replacements != 1:
        raise RuntimeError("could not find the native ChatGPT usage menu slot")
    path.write_text(source, encoding="utf-8")


def patch_profile_query(extracted: Path) -> None:
    path = find_asset(
        renderer_asset_files(extracted),
        b'CodexRequest.safeGet("/wham/profiles/me")',
        "native profile statistics request",
        required=False,
    )
    if path is None:
        return
    source = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'await ([A-Za-z0-9_$]+)\.CodexRequest\.safeGet\("/wham/profiles/me"\)'
    )
    if len(pattern.findall(source)) != 1:
        raise RuntimeError("could not find the native profile statistics request")
    source = pattern.sub(
        r"await (globalThis.codexMuxProfileData?"
        r"globalThis.codexMuxProfileData("
        r"globalThis.__codexMuxSelectedProfileAccountId??null):"
        r'\1.CodexRequest.safeGet("/wham/profiles/me"))',
        source,
        count=1,
    )
    path.write_text(source, encoding="utf-8")


def patch_renderer(extracted: Path, token: str) -> None:
    patch_renderer_csp(extracted)
    patch_account_menu(extracted, token)
    patch_profile_query(extracted)


def patch_desktop_profile(
    extracted: Path, installed_computer_use_app: Path
) -> None:
    """Give the copied Electron app its own user-data and single-instance scope."""
    computer_use_pipe = json.dumps(str(DEFAULT_STATE_ROOT / "computer-use.sock"))
    computer_use_app = json.dumps(str(installed_computer_use_app))
    profile_root = (
        Path.home() / "Library" / "Application Support" / DESKTOP_PROFILE_NAME
    )
    environment_prefix = (
        f"process.env.SKY_CUA_SERVICE_NATIVE_PIPE_PATH={computer_use_pipe};"
        f"process.env.SKY_CUA_SERVICE_PATH={computer_use_app};"
        f"process.env.CODEX_ELECTRON_COMPUTER_USE_APP_PATH={computer_use_app};"
        "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH="
        + chr(96) + "1" + chr(96) + ";"
        "process.env.CODEX_ELECTRON_USER_DATA_PATH="
        + json.dumps(str(profile_root))
        + ";"
    )

    build_files = build_asset_files(extracted)

    profile_path = find_asset(
        build_files,
        b'app.setPath("userData",function({appDataPath:',
        "ChatGPT desktop profile bootstrap",
    )
    if profile_path is None:
        raise RuntimeError("could not isolate the copied ChatGPT desktop profile")
    profile_source = profile_path.read_text(encoding="utf-8")
    profile_pattern = re.compile(
        r"(?P<electron>[A-Za-z_$][\w$]*)\.app\.setPath\("
        r'"userData",function\(\{appDataPath:'
    )
    if len(profile_pattern.findall(profile_source)) != 1:
        raise RuntimeError("could not isolate the copied ChatGPT desktop profile")
    profile_source = profile_pattern.sub(
        environment_prefix
        + r'\g<electron>.app.setPath("userData",function({appDataPath:',
        profile_source,
        count=1,
    )

    # The copied app must never replace itself with an unpatched official update.
    updater_pattern = re.compile(
        r'(?P<prefix>phase:"bootstrap-import-main"\};try\{)if\(await '
        r"[A-Za-z0-9_$]+\.initialize\(\),"
    )
    profile_source, updater_replacements = updater_pattern.subn(
        r"\g<prefix>if(", profile_source, count=1
    )
    if updater_replacements != 1:
        raise RuntimeError("could not disable updates in the copied ChatGPT app")
    profile_path.write_text(profile_source, encoding="utf-8")

    main_path = find_asset(
        build_files, b",{onServiceAvailable:", "ChatGPT desktop main runtime"
    )
    if main_path is None:
        raise RuntimeError("could not pin the managed Computer Use service")
    main = main_path.read_text(encoding="utf-8")
    managed_service_pattern = re.compile(
        r"(?P<prefix>[A-Za-z_$][\w$]*=new [A-Za-z_$][\w$]*\()"
        r"[A-Za-z_$][\w$]*\([A-Za-z_$][\w$]*\.codexHome\)"
        r"(?P<suffix>,\{onServiceAvailable:)"
    )
    main, managed_service_replacements = managed_service_pattern.subn(
        lambda match: (
            match.group("prefix")
            + json.dumps(str(installed_computer_use_app))
            + match.group("suffix")
        ),
        main,
        count=1,
    )
    if managed_service_replacements != 1:
        raise RuntimeError(
            "could not pin the managed Computer Use service to its installed app"
        )

    computer_use_instruction = "Control desktop apps on macOS through Computer Use."
    strict_computer_use_instruction = (
        "Control desktop apps on macOS through Computer Use via node_repl and "
        "@oai/sky only. Never use shell commands, open, AppleScript, osascript, "
        "JXA, System Events, or CGEvent synthesis for computer interactions or "
        "as a fallback. If Computer Use is unavailable, report the failure "
        "instead of using another automation method."
    )
    if main.count(computer_use_instruction) != 1:
        raise RuntimeError("could not find the Computer Use tool instruction")
    main = main.replace(
        computer_use_instruction,
        strict_computer_use_instruction,
        1,
    )
    ui_test_bridge = main_path.parent / "ui-test-bridge.cjs"
    shutil.copy2(PROJECT_ROOT / "ui" / "ui-test-bridge.cjs", ui_test_bridge)
    main += (
        "\n;if(process.env.CODEX_MUX_UI_TESTS==="
        + chr(96) + "1" + chr(96) + ")"
        "require(require(" + chr(96) + "node:path" + chr(96) + ").join(__dirname,"
        + chr(96) + "ui-test-bridge.cjs" + chr(96) + ")).start();"
    )
    main_path.write_text(main, encoding="utf-8")


def asar_header_digest(asar_path: Path) -> str:
    """Hash the ASAR header block, which is what ElectronAsarIntegrity records.

    Electron validates the packed header (the JSON describing every entry), not
    the whole archive; hashing the whole file makes the copy fail its own
    integrity check at launch with "Failed to get integrity".
    """
    with asar_path.open("rb") as handle:
        prefix = handle.read(16)
        if len(prefix) != 16:
            raise RuntimeError(f"truncated ASAR header in {asar_path}")
        header_length = struct.unpack("<I", prefix[12:16])[0]
        header = handle.read(header_length)
        if len(header) != header_length:
            raise RuntimeError(f"truncated ASAR header in {asar_path}")
    return hashlib.sha256(header).hexdigest()


INTEGRITY_DIGEST_SENTINEL = b"AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A"


def asar_integrity_dictionary_digest(info: dict[str, object]) -> bytes:
    """Digest the ASAR integrity dictionary the way Electron's packer does."""
    integrity = info.get("ElectronAsarIntegrity") or {}
    hasher = hashlib.sha256()
    for key in sorted(integrity, key=lambda value: value.encode("utf-16-be")):
        payload = integrity[key]
        hasher.update(key.encode())
        hasher.update(str(payload.get("algorithm", "")).encode())
        hasher.update(str(payload.get("hash", "")).encode())
    return hasher.digest()


def sync_asar_integrity_digest(app: Path, info: dict[str, object]) -> None:
    """Re-seal the integrity dictionary stored inside the Electron framework.

    Newer Electron builds embed a digest of Info.plist's ElectronAsarIntegrity
    dictionary in the framework's __asar_integrity section. Editing the archive
    hash without updating that digest makes the copy die at launch with
    "Failed to get integrity for validatable asar archive".
    """
    digest = asar_integrity_dictionary_digest(info)
    slot = INTEGRITY_DIGEST_SENTINEL + b"\x01\x01" + digest
    sealed = False
    for framework in (app / "Contents" / "Frameworks").glob("*.framework"):
        executable = bundle_main_executable(framework)
        if executable is None:
            continue
        binary = executable.read_bytes()
        if INTEGRITY_DIGEST_SENTINEL not in binary:
            continue
        if binary.count(INTEGRITY_DIGEST_SENTINEL) != 1:
            raise RuntimeError(
                f"ambiguous ASAR integrity digest slot in {executable.name}"
            )
        start = binary.index(INTEGRITY_DIGEST_SENTINEL)
        end = start + len(slot)
        if len(binary) < end:
            raise RuntimeError(f"truncated ASAR integrity digest slot in {executable}")
        executable.write_bytes(binary[:start] + slot + binary[end:])
        sealed = True
    if not sealed:
        print(
            "Warning: this ChatGPT build carries no embedded ASAR integrity "
            "digest; leaving the plist hash as the only validation input.",
            file=sys.stderr,
        )


def patch_info_plist(
    app: Path,
    asar_path: Path,
    team_identifier: str | None,
) -> None:
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleDisplayName"] = "Codex Subscription Router"
    info["CFBundleName"] = "Codex Subscription Router"
    # A distinct identifier keeps Launch Services and external Computer Use from
    # confusing this independently signed copy with the official ChatGPT app.
    info["CFBundleIdentifier"] = DESKTOP_BUNDLE_IDENTIFIER
    info["CFBundleExecutable"] = "CodexSubscriptionRouterLauncher"
    info["BundleSigningBaseName"] = "CodexSubscriptionRouter"
    info["CodexMuxSigningTeamIdentifier"] = team_identifier or "adhoc"
    info["CrProductDirName"] = DESKTOP_PROFILE_NAME
    for key in list(info):
        if key.startswith("SU"):
            del info[key]
    info["SUEnableAutomaticChecks"] = False
    info["SUAllowsAutomaticUpdates"] = False
    for url_type in info.get("CFBundleURLTypes", []):
        schemes = url_type.get("CFBundleURLSchemes", [])
        url_type["CFBundleURLSchemes"] = [
            "codex-subscription-router" if value == "codex" else value for value in schemes
        ]
    digest = asar_header_digest(asar_path)
    info["ElectronAsarIntegrity"] = {
        "Resources/app.asar": {"algorithm": "SHA256", "hash": digest}
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)
    sync_asar_integrity_digest(app, info)


def patch_app(
    source: Path,
    destination: Path,
    force: bool,
    allow_adhoc_signing: bool,
    allow_untested_source: bool,
    allow_signing_team_change: bool,
) -> None:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_dir() or not (source / "Contents" / "Resources" / "app.asar").is_file():
        raise RuntimeError(f"not a ChatGPT app bundle: {source}")
    if source == destination:
        raise RuntimeError(
            "source and destination must be different; "
            "the original app is never patched in place"
        )
    if destination.exists() and not force:
        raise RuntimeError(
            f"destination exists: {destination} "
            "(pass --force to create a recoverable backup)"
        )

    source_plist = source / "Contents" / "Info.plist"
    with source_plist.open("rb") as handle:
        source_info = plistlib.load(handle)
    source_version = str(source_info.get("CFBundleShortVersionString", "unknown"))
    source_build = str(source_info.get("CFBundleVersion", "unknown"))
    source_asar = source / "Contents" / "Resources" / "app.asar"
    source_asar_hash = hashlib.sha256(source_asar.read_bytes()).hexdigest()
    expected_asar_hash = TESTED_SOURCE_BUILDS.get((source_version, source_build))
    print(
        f"Source ChatGPT version: {source_version} ({source_build}), "
        f"app.asar {source_asar_hash}"
    )
    if expected_asar_hash != source_asar_hash and not allow_untested_source:
        raise RuntimeError(
            "the source version, build, or app.asar hash is not approved; "
            "review the upstream change or pass --allow-untested-source"
        )
    if expected_asar_hash != source_asar_hash:
        print(
            "Warning: continuing with an untested official ChatGPT build; "
            "the patch will continue only while every expected anchor matches.",
            file=sys.stderr,
        )

    for tool in ("codesign", "ditto", "go", "npm", "security", "xcrun"):
        require_tool(tool)
    asar = ensure_asar_tool()
    token = load_or_create_token()
    signing_identity = resolve_signing_identity(allow_adhoc_signing)
    global ADHOC_SIGNING
    ADHOC_SIGNING = signing_identity == "-"
    team_identifier = signing_team_identifier(signing_identity)
    if destination.exists():
        installed_team = existing_signing_team(destination)
        if installed_team != team_identifier and not allow_signing_team_change:
            raise RuntimeError(
                "the selected signing team differs from the installed build; "
                "reuse the prior identity or pass --allow-signing-team-change"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    installed_computer_use_app = destination.parent / COMPUTER_USE_APP_NAME
    if force:
        ensure_components_are_stopped((destination, installed_computer_use_app))

    with tempfile.TemporaryDirectory(prefix=".codex-subscription-router-", dir=destination.parent) as temporary:
        temporary_path = Path(temporary)
        staged_app = temporary_path / destination.name
        staged_computer_use_app = temporary_path / COMPUTER_USE_APP_NAME
        extracted = temporary_path / "asar"
        proxy = temporary_path / "codex-mux"

        print("Building multiplexer…")
        build_proxy(proxy)
        print("Copying ChatGPT.app…")
        run(["ditto", str(source), str(staged_app)])
        install_launcher(staged_app)

        resources = staged_app / "Contents" / "Resources"
        original_asar = resources / "app.asar"
        print("Patching desktop profile and renderer…")
        run([str(asar), "extract", str(original_asar), str(extracted)])
        patch_asar_computer_use_identity(
            extracted,
            ASAR_CUA_IDENTIFIER_REPLACEMENTS.get((source_version, source_build)),
        )
        patch_desktop_profile(extracted, installed_computer_use_app)
        patch_renderer(extracted, token)
        sign_native_code_tree(extracted, signing_identity)
        repacked_asar = temporary_path / "app.asar"
        run(
            [
                str(asar),
                "pack",
                "--unpack-dir",
                ASAR_UNPACK_DIRECTORIES,
                str(extracted),
                str(repacked_asar),
            ]
        )
        asar_listing = output([str(asar), "list", "--is-pack", str(repacked_asar)])
        required_unpacked_module = (
            "unpack : /node_modules/better-sqlite3/build/Release/"
            "better_sqlite3.node"
        )
        if required_unpacked_module not in asar_listing:
            raise RuntimeError("native ASAR modules were not kept unpacked")
        shutil.copy2(repacked_asar, original_asar)
        repacked_unpacked = temporary_path / "app.asar.unpacked"
        if not repacked_unpacked.is_dir():
            raise RuntimeError("ASAR pack did not produce its unpacked native tree")
        shutil.copytree(
            repacked_unpacked,
            resources / "app.asar.unpacked",
            dirs_exist_ok=True,
        )

        bundled_codex = codex_cli_entrypoint(resources)
        if codex_cli_supports_proxy(bundled_codex):
            real_codex = bundled_codex.with_name(bundled_codex.name + ".real")
            if real_codex.exists():
                raise RuntimeError(
                    f"source app already contains {real_codex.name} next to the CLI"
                )
            bundled_codex.rename(real_codex)
            shutil.copy2(proxy, bundled_codex)
            bundled_codex.chmod(0o755)
        else:
            print(
                "Warning: this ChatGPT build launches its CLI from a nested "
                "CodexCLI.app that the desktop provisions and validates "
                "itself. The app is patched, but subscription multiplexing is "
                "inactive on this build.",
                file=sys.stderr,
            )

        patch_info_plist(staged_app, original_asar, team_identifier)
        print(f"Signing independent app copy with {signing_identity}…")
        sign_independent_app(staged_app, signing_identity, team_identifier)
        verify_signed_code(
            staged_app,
            DESKTOP_BUNDLE_IDENTIFIER,
            team_identifier,
        )
        verify_signed_code(
            staged_app / "Contents" / "MacOS" / "ChatGPT",
            OPENAI_DESKTOP_CODE_IDENTIFIER,
            team_identifier,
        )
        bundled_computer_use_app = (
            computer_use_package(staged_app) / "Codex Computer Use.app"
        )
        run(
            [
                "ditto",
                str(bundled_computer_use_app),
                str(staged_computer_use_app),
            ]
        )
        verify_signed_code(
            staged_computer_use_app,
            COMPUTER_USE_BUNDLE_IDENTIFIER,
            team_identifier,
        )

        backup_suffix = time.strftime("%Y%m%d-%H%M%S")
        backup_directory = DEFAULT_STATE_ROOT / "backups" / backup_suffix
        app_backup = backup_directory / destination.name
        helper_backup = backup_directory / installed_computer_use_app.name
        had_app = destination.exists()
        had_helper = installed_computer_use_app.exists()
        if had_app or had_helper:
            backup_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup_directory.parent.chmod(0o700)
            backup_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        try:
            if had_app:
                destination.rename(app_backup)
                print(f"Existing copy moved to {app_backup}")
            if had_helper:
                installed_computer_use_app.rename(helper_backup)
                print(f"Existing Computer Use helper moved to {helper_backup}")
            staged_app.rename(destination)
            staged_computer_use_app.rename(installed_computer_use_app)
        except OSError:
            failed_directory = backup_directory / "failed-install"
            failed_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if destination.exists():
                destination.rename(failed_directory / destination.name)
            if installed_computer_use_app.exists():
                installed_computer_use_app.rename(
                    failed_directory / installed_computer_use_app.name
                )
            if app_backup.exists():
                app_backup.rename(destination)
            if helper_backup.exists():
                helper_backup.rename(installed_computer_use_app)
            raise

    if LAUNCH_SERVICES_REGISTER.is_file():
        run(
            [
                str(LAUNCH_SERVICES_REGISTER),
                "-f",
                str(destination),
                str(installed_computer_use_app),
            ]
        )
    retire_stale_cached_computer_use_app()

    print(destination)
    print(installed_computer_use_app)


def main() -> int:
    args = parse_args()
    try:
        patch_app(
            args.source,
            args.destination,
            args.force,
            args.allow_adhoc_signing,
            args.allow_untested_source,
            args.allow_signing_team_change,
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
