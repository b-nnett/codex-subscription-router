#!/usr/bin/env python3
"""Create an independently signed ChatGPT.app copy with Codex multiplexing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
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

SUPPORTED_ARCHITECTURES = (
    "arm64",
    "x86_64",
)
GOARCH_BY_ARCHITECTURE = {
    "arm64": "arm64",
    "x86_64": "amd64",
}
CLANG_ARCH_BY_ARCHITECTURE = {
    "arm64": "arm64",
    "x86_64": "x86_64",
}


@dataclass(frozen=True)
class SourceCompatibility:
    """A tested official ChatGPT source bundle for one CPU architecture."""

    version: str
    build: str
    architecture: str
    asar_sha256: str
    computer_use_supported: bool
    cua_identifier_replacements: int = 49
    asar_cua_identifier_replacements: int = 17


TESTED_SOURCES: tuple[SourceCompatibility, ...] = (
    SourceCompatibility(
        version="26.803.61601",
        build="6396",
        architecture="arm64",
        asar_sha256="d5a44ed9e2f1db5f81dbbe85408aed256f3203c5b16f00817bb9d7cd941343cf",
        computer_use_supported=True,
    ),
    SourceCompatibility(
        version="26.903.61454",
        build="8378",
        architecture="x86_64",
        asar_sha256="a5f9500e023a65f98dafca3a24a3c0fdf23646caf961b8b3df960d6057e7b450",
        computer_use_supported=False,
    ),
)
# Backwards-compatible lookup keyed by (version, build) for the original
# Apple-silicon source. New code must use TESTED_SOURCES / lookup functions
# below so that compatibility stays architecture aware.
TESTED_SOURCE_BUILDS = {
    (source.version, source.build): source.asar_sha256
    for source in TESTED_SOURCES
    if source.architecture == "arm64"
}
EXPECTED_CUA_IDENTIFIER_REPLACEMENTS = 49
EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS = 17


def host_architecture() -> str:
    """Return the host CPU architecture using Mach-O slice names."""
    return output(["uname", "-m"])


def goarch_for_architecture(architecture: str) -> str:
    """Map a Mach-O architecture name to the Go GOARCH value."""
    try:
        return GOARCH_BY_ARCHITECTURE[architecture]
    except KeyError:
        raise RuntimeError(
            f"unsupported target architecture: {architecture!r}; "
            f"supported architectures: {', '.join(SUPPORTED_ARCHITECTURES)}"
        ) from None


def clang_arch_for_architecture(architecture: str) -> str:
    """Map a Mach-O architecture name to the clang -arch value."""
    try:
        return CLANG_ARCH_BY_ARCHITECTURE[architecture]
    except KeyError:
        raise RuntimeError(
            f"unsupported target architecture: {architecture!r}; "
            f"supported architectures: {', '.join(SUPPORTED_ARCHITECTURES)}"
        ) from None


def macho_architectures(path: Path) -> set[str]:
    """Return the CPU slices present in a Mach-O file."""
    if not is_mach_o(path):
        raise RuntimeError(f"not a Mach-O binary: {path}")
    try:
        return set(output(["lipo", "-archs", str(path)]).split())
    except (subprocess.CalledProcessError, OSError) as error:
        raise RuntimeError(f"could not inspect architectures of {path}: {error}") from error


def detect_source_architecture(app: Path) -> str:
    """Detect the usable architecture of an official ChatGPT bundle.

    A universal bundle resolves to the host slice. A bundle without the host
    slice is rejected instead of being silently rebuilt for another CPU.
    """
    plist_path = app / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as handle:
            executable_name = plistlib.load(handle).get("CFBundleExecutable", "ChatGPT")
    except (OSError, plistlib.InvalidFileException) as error:
        raise RuntimeError(f"could not read {plist_path}: {error}") from error
    if not isinstance(executable_name, str) or executable_name == "":
        raise RuntimeError(f"could not determine the main executable of {app}")
    executable = app / "Contents" / "MacOS" / executable_name
    if not is_mach_o(executable):
        raise RuntimeError(
            f"could not determine the source architecture: {executable} "
            "is not a Mach-O binary"
        )
    slices = macho_architectures(executable)
    host = host_architecture()
    if host in slices:
        if host not in SUPPORTED_ARCHITECTURES:
            raise RuntimeError(
                f"unsupported Mac architecture {host!r}; "
                f"supported architectures: {', '.join(SUPPORTED_ARCHITECTURES)}"
            )
        return host
    raise RuntimeError(
        f"official ChatGPT executable has slices {sorted(slices)} "
        f"but this Mac needs {host!r}; refusing to rebuild across architectures"
    )


def lookup_source_compatibility(
    version: str,
    build: str,
    architecture: str,
    asar_sha256: str,
) -> SourceCompatibility | None:
    """Return the tested compatibility profile for an exact source, if any."""
    for source in TESTED_SOURCES:
        if (
            source.version == version
            and source.build == build
            and source.architecture == architecture
            and source.asar_sha256 == asar_sha256
        ):
            return source
    return None


def validate_macho_tree_for_architecture(root: Path, architecture: str) -> list[Path]:
    """Fail fast when any Mach-O file under root lacks the target slice.

    `codesign` never converts one CPU architecture into another, so this check
    must run before signing. Returns the checked Mach-O files.
    """
    if architecture not in SUPPORTED_ARCHITECTURES:
        raise RuntimeError(
            f"unsupported target architecture: {architecture!r}; "
            f"supported architectures: {', '.join(SUPPORTED_ARCHITECTURES)}"
        )
    checked: list[Path] = []
    if not root.exists():
        return checked
    for candidate in sorted(root.rglob("*")):
        if not is_mach_o(candidate):
            continue
        slices = macho_architectures(candidate)
        if architecture not in slices:
            raise RuntimeError(
                f"{candidate} provides slices {sorted(slices)}; "
                f"target architecture {architecture!r} is missing"
            )
        checked.append(candidate)
    return checked


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
    parser.add_argument(
        "--enable-computer-use",
        action="store_true",
        help=(
            "Attempt a full build with the independent Computer Use helper. "
            "Without a tested compatibility profile this stays fail-closed; "
            "the default for untested sources is a Core build without Computer Use."
        ),
    )
    parser.add_argument(
        "--print-install-plan",
        action="store_true",
        help=(
            "Print the detected source architecture, capability level, and "
            "minimum macOS as key=value lines without building anything. "
            "Used by install.sh for the capability-aware OS requirement."
        ),
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


def patch_arm64_team_identity(executable: Path, team_identifier: str) -> None:
    """Patch the caller-team checks in an Apple-silicon Computer Use binary."""
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


def patch_x86_64_team_identity(executable: Path, team_identifier: str) -> None:
    """Patch the caller-team checks in an Intel Computer Use binary.

    The x86_64 instruction sequence that materializes the 10-byte Swift
    small-string team constants differs from the ARM64 MOVZ/MOVK sequence, so
    the ARM64 byte patterns must never be applied here. The Intel sequence has
    not been reverse engineered from an official Intel SkyComputerUseService
    yet, therefore this stays fail-closed until a deterministic pattern with
    exact occurrence counts is proven. Build in Core mode instead.
    """
    _ = (executable, team_identifier)
    raise RuntimeError(
        "the x86_64 Computer Use team-identity patch is not implemented; "
        "the Intel SkyComputerUseService instruction sequence has not been "
        "reverse engineered yet. Rebuild without --enable-computer-use for a "
        "Core build, which leaves Computer Use disabled instead of patching "
        "the binary with an unproven pattern."
    )


def patch_computer_use_team_identity(
    executable: Path,
    architecture: str,
    team_identifier: str,
) -> None:
    """Dispatch the Computer Use binary patch to the CPU-specific implementation."""
    if architecture == "arm64":
        patch_arm64_team_identity(executable, team_identifier)
    elif architecture == "x86_64":
        patch_x86_64_team_identity(executable, team_identifier)
    else:
        raise RuntimeError(
            f"unsupported Computer Use architecture: {architecture!r}; "
            f"supported architectures: {', '.join(SUPPORTED_ARCHITECTURES)}"
        )


def patch_computer_use_identity(
    app: Path,
    team_identifier: str | None,
    *,
    architecture: str = "arm64",
    expected_identifier_replacements: int = EXPECTED_CUA_IDENTIFIER_REPLACEMENTS,
) -> None:
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
    if identifier_replacements != expected_identifier_replacements:
        raise RuntimeError(
            "expected "
            f"{expected_identifier_replacements} Computer Use identity "
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
    patch_computer_use_team_identity(executable, architecture, team_identifier)


def patch_asar_computer_use_identity(
    extracted: Path,
    *,
    expected_replacements: int = EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS,
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
    if replacements != expected_replacements:
        raise RuntimeError(
            "expected "
            f"{expected_replacements} Computer Use references "
            f"in app.asar, found {replacements}"
        )


def sign_native_code_tree(root: Path, identity: str) -> None:
    """Sign native modules before ASAR records their final sizes."""
    if not root.is_dir():
        return
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
                "--options",
                "runtime",
                str(candidate),
            ]
        )


TEAM_SCOPED_ENTITLEMENTS = (
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
    "com.apple.security.application-groups",
    "keychain-access-groups",
)


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


def sign_main_executable(app: Path, identity: str) -> None:
    """Re-sign the desktop main executable after the bundle Info.plist changes.

    The bundle seal covers the modified Info.plist, so the main executable
    must be re-signed too; otherwise `--deep --strict` verification reports an
    invalid Info.plist. This is independent of Computer Use.
    """
    sign_runtime_executable(
        app / "Contents" / "MacOS" / "ChatGPT",
        identity,
        OPENAI_DESKTOP_CODE_IDENTIFIER,
        runtime=False,
    )


def sign_independent_app(
    app: Path,
    identity: str,
    team_identifier: str | None,
    *,
    architecture: str = "arm64",
    expected_cua_identifier_replacements: int = EXPECTED_CUA_IDENTIFIER_REPLACEMENTS,
    computer_use_enabled: bool = True,
) -> None:
    """Apply one stable identity throughout the modified Electron bundle."""
    if computer_use_enabled:
        computer_use_entitlements = capture_computer_use_entitlements(app)
        patch_computer_use_identity(
            app,
            team_identifier,
            architecture=architecture,
            expected_identifier_replacements=expected_cua_identifier_replacements,
        )
        sign_computer_use_code(app, identity, computer_use_entitlements)
    sign_main_executable(app, identity)
    run(
        [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp=none",
            str(app / "Contents" / "Resources" / "codex"),
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


def build_proxy(destination: Path, architecture: str) -> None:
    """Build the Go multiplexer deterministically for the target architecture."""
    goarch = goarch_for_architecture(architecture)
    destination.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["GOOS"] = "darwin"
    environment["GOARCH"] = goarch
    environment.pop("GOFLAGS", None)
    subprocess.run(
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
        check=True,
        env=environment,
    )
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    slices = macho_architectures(destination)
    if architecture not in slices:
        raise RuntimeError(
            f"built codex-mux provides slices {sorted(slices)}; "
            f"target architecture {architecture!r} is missing"
        )


def install_launcher(app: Path, architecture: str) -> None:
    """Pass Chromium its isolated profile before Electron's main process starts."""
    clang_arch = clang_arch_for_architecture(architecture)
    launcher = app / "Contents" / "MacOS" / "CodexSubscriptionRouterLauncher"
    run(
        [
            "xcrun",
            "clang",
            "-arch",
            clang_arch,
            "-Os",
            "-Wall",
            "-Wextra",
            "-o",
            str(launcher),
            str(PROJECT_ROOT / "native" / "launcher.c"),
        ]
    )
    slices = macho_architectures(launcher)
    if architecture not in slices:
        raise RuntimeError(
            f"built launcher provides slices {sorted(slices)}; "
            f"target architecture {architecture!r} is missing"
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


def require_unique(text: str, anchor: str, description: str) -> None:
    """Fail closed unless an anchor occurs exactly once."""
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(
            f"could not find {description} "
            f"(found {count}; expected exactly 1)"
        )


def replace_unique(text: str, anchor: str, replacement: str, description: str) -> str:
    """Replace an anchor that must occur exactly once."""
    require_unique(text, anchor, description)
    return text.replace(anchor, replacement, 1)


def replace_identifier(
    text: str, old: str, new: str, expected: int, description: str
) -> str:
    """Rename an identifier, matching whole tokens only (never substrings)."""
    pattern = re.compile(rf"(?<![\w$]){re.escape(old)}(?![\w$])")
    matches = pattern.findall(text)
    if len(matches) != expected:
        raise RuntimeError(
            f"could not map {description}: found {len(matches)} "
            f"occurrences of {old!r}, expected exactly {expected}"
        )
    return pattern.sub(lambda _: new, text)


@dataclass(frozen=True)
class RendererCompatibility:
    """A tested renderer anchor set for one build and architecture."""

    version: str
    build: str
    architecture: str
    layout: str  # "legacy" (6396 single bundle) or "split" (8378 two bundles)


RENDERER_PROFILES: tuple[RendererCompatibility, ...] = (
    RendererCompatibility(
        version="26.803.61601",
        build="6396",
        architecture="arm64",
        layout="legacy",
    ),
    RendererCompatibility(
        version="26.903.61454",
        build="8378",
        architecture="x86_64",
        layout="split",
    ),
)


def lookup_renderer_compatibility(
    version: str,
    build: str,
    architecture: str,
) -> RendererCompatibility | None:
    """Return the renderer anchor set for an exact build, if one is known."""
    for profile in RENDERER_PROFILES:
        if (
            profile.version == version
            and profile.build == build
            and profile.architecture == architecture
        ):
            return profile
    return None


# Minimum macOS versions follow measured deployment targets, not guesses:
# Intel Core build 8378 declares LSMinimumSystemVersion 13.0 with minos 13.0
# on the main executable, the Electron framework, and every helper, except
# the bundled cua Node runtime at minos 13.5 (verified with otool
# LC_BUILD_VERSION). Untested Intel sources and any Full (Computer Use)
# build stay at the conservative 14.0 floor. Apple-silicon keeps the
# historical upstream policy (no installer floor).
INTEL_CORE_MINIMUM_MACOS = (13, 5)
INTEL_FULL_MINIMUM_MACOS = (14, 0)


def minimum_macos_for_capability(
    architecture: str, computer_use_enabled: bool
) -> tuple[int, int] | None:
    """Return the minimum (major, minor) macOS for an arch/capability pair.

    Returns None when the installer enforces no floor for that pair.
    """
    if architecture == "x86_64":
        if computer_use_enabled:
            return INTEL_FULL_MINIMUM_MACOS
        return INTEL_CORE_MINIMUM_MACOS
    if architecture == "arm64":
        return None
    raise RuntimeError(
        f"unsupported target architecture: {architecture!r}; "
        f"supported architectures: {', '.join(SUPPORTED_ARCHITECTURES)}"
    )


def install_plan(source: Path, enable_computer_use: bool = False) -> dict[str, str]:
    """Describe the planned build without changing anything on disk.

    Values are best-effort "unknown" strings when the source cannot be read;
    capability is "core", "full", or "untested" (an untested source never
    reports a tested capability, so the installer stays conservative).
    """
    resolved = source.expanduser()
    version = build = architecture = asar_sha256 = "unknown"
    capability = "untested"
    try:
        with (resolved / "Contents" / "Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
        version = str(info.get("CFBundleShortVersionString", "unknown"))
        build = str(info.get("CFBundleVersion", "unknown"))
        architecture = detect_source_architecture(resolved)
        asar_sha256 = hashlib.sha256(
            (resolved / "Contents" / "Resources" / "app.asar").read_bytes()
        ).hexdigest()
    except (OSError, plistlib.InvalidFileException, RuntimeError):
        pass
    compatibility = (
        lookup_source_compatibility(version, build, architecture, asar_sha256)
        if architecture in SUPPORTED_ARCHITECTURES
        else None
    )
    if compatibility is not None:
        capability = "full" if compatibility.computer_use_supported else "core"
    # An untested source never gets the relaxed Core floor: without a proven
    # anchor set and deployment-target audit, Intel stays conservative.
    conservative_full = capability == "full" or (
        capability == "untested" and architecture == "x86_64"
    )
    try:
        minimum = minimum_macos_for_capability(
            architecture, conservative_full or enable_computer_use
        )
    except RuntimeError:
        minimum = INTEL_FULL_MINIMUM_MACOS if architecture == "x86_64" else None
    return {
        "source": str(resolved),
        "source_version": version,
        "source_build": build,
        "architecture": architecture,
        "capability": capability,
        "minimum_macos": (
            "none" if minimum is None else f"{minimum[0]}.{minimum[1]}"
        ),
    }


def check_js_module_syntax(path: Path) -> None:
    """Validate a patched ESM bundle with node --check via a .mjs copy."""
    with tempfile.TemporaryDirectory(prefix=".codex-syntax-") as temporary:
        probe = Path(temporary) / (path.stem + ".syntax-check.mjs")
        shutil.copy2(path, probe)
        subprocess.run(["node", "--check", str(probe)], check=True)


def patch_renderer(
    extracted: Path,
    token: str,
    renderer: RendererCompatibility | None,
) -> None:
    """Dispatch renderer patching to the build-specific anchor set.

    An unknown build keeps the legacy anchor set, which stays fail-closed on
    unfamiliar bundles instead of guessing.
    """
    patch_renderer_csp(extracted, token)
    if renderer is not None and renderer.layout == "split":
        patch_renderer_8378(extracted, token)
    else:
        patch_renderer_legacy(extracted, token)


def patch_renderer_csp(extracted: Path, token: str) -> None:
    webview = extracted / "webview"
    index_path = webview / "index.html"
    index = index_path.read_text(encoding="utf-8")

    connect_anchor = "connect-src &#39;self&#39;"
    if connect_anchor not in index:
        raise RuntimeError("could not find ChatGPT renderer CSP connect-src")
    index = index.replace(
        connect_anchor,
        f"{connect_anchor} http://127.0.0.1:{CONTROL_PORT}",
        1,
    )
    index_path.write_text(index, encoding="utf-8")


def patch_renderer_legacy(extracted: Path, token: str) -> None:
    """Apply the build-6396 single-bundle anchor set (Apple silicon)."""
    webview = extracted / "webview"
    initial_bundles = list((webview / "assets").glob("app-initial-*.js"))
    if len(initial_bundles) != 1:
        raise RuntimeError(
            f"expected one ChatGPT initial renderer bundle, found {len(initial_bundles)}"
        )
    bundle_path = initial_bundles[0]
    bundle = bundle_path.read_text(encoding="utf-8")
    if "function CodexMuxAccountMenu(" in bundle:
        raise RuntimeError("source app already contains the Codex multiplexer menu")

    component = (PROJECT_ROOT / "ui" / "account-menu.js").read_text(encoding="utf-8")
    component = component.replace("__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT))
    component = component.replace("__CODEX_MUX_CONTROL_TOKEN__", token)
    component_anchor = "function wXc({sidebarFooter:e,triggerButton:t})"
    if bundle.count(component_anchor) != 1:
        raise RuntimeError("could not find the native ChatGPT profile menu component")
    bundle = bundle.replace(component_anchor, component + "\n" + component_anchor, 1)

    plugin_rpc_mapping_anchors = (
        '"list-apps":q9((e,{priority:t,source:n,timeoutMs:r,trace:i,...a})=>'
        "e.sendRequest(`app/list`,a,",
        '"list-installed-apps":q9((e,t)=>e.sendRequest(`app/installed`,t))',
        '"read-apps":q9((e,t)=>e.sendRequest(`app/read`,t))',
        '"login-mcp-server":q9((e,t)=>'
        "e.sendRequest(`mcpServer/oauth/login`,t))",
        '"list-mcp-server-status":K9((e,{priority:t,source:n,timeoutMs:r,'
        "trace:i,...a})=>e.listMcpServers(a,",
        "listMcpServers(e,t){let n=JSON.stringify({options:t,params:e})",
        "let i=this.sendRequest(`mcpServerStatus/list`,e,t);",
    )
    for mapping_anchor in plugin_rpc_mapping_anchors:
        if bundle.count(mapping_anchor) != 1:
            raise RuntimeError(
                "could not verify the native Plugins request-to-RPC mapping"
            )

    app_server_request_anchor = (
        "function gm(e,t,n){return n==null?h6e.sendRequest(e,t):"
        "h6e.sendRequest(e,t,n)}"
    )
    if bundle.count(app_server_request_anchor) != 1:
        raise RuntimeError("could not find the native app-server request bridge")
    bundle = bundle.replace(
        app_server_request_anchor,
        "function gm(e,t,n){let r=codexMuxScopePluginRequest(e,t);"
        "return n==null?h6e.sendRequest(e,r):h6e.sendRequest(e,r,n)}",
        1,
    )

    profile_query_anchor = "let e=await T_.safeGet(`/wham/profiles/me`)"
    if bundle.count(profile_query_anchor) != 1:
        raise RuntimeError("could not find the native profile stats request")
    bundle = bundle.replace(
        profile_query_anchor,
        "let e=await codexMuxProfileData("
        "globalThis.__codexMuxSelectedProfileAccountId??null)",
        1,
    )

    native_usage_modal_anchor = "function QLs(e){"
    if bundle.count(native_usage_modal_anchor) != 1:
        raise RuntimeError("could not find the native Usage modal component")
    bundle = bundle.replace(
        native_usage_modal_anchor,
        "function QLs(e){CodexMuxUseResetAccountState();",
        1,
    )

    reset_query_anchor = (
        "function l6r(){let e=(0,$F.c)(1),t;return "
        "e[0]===Symbol.for(`react.memo_cache_sentinel`)?"
        "(t={queryKey:[`rate-limit-reset-credits`],queryFn:u6r,"
        "refetchInterval:vm.ONE_MINUTE,staleTime:vm.FIVE_SECONDS},e[0]=t):"
        "t=e[0],Lt(t)}"
    )
    if bundle.count(reset_query_anchor) != 1:
        raise RuntimeError("could not find the native reset-credit query")
    bundle = bundle.replace(
        reset_query_anchor,
        "function l6r(){let e=window.__codexMuxResetAccountId;return Lt({"
        "queryKey:[`rate-limit-reset-credits`,e??`primary`],"
        "queryFn:e?()=>codexMuxRateLimitResets(e):u6r,"
        "refetchInterval:vm.ONE_MINUTE,staleTime:vm.FIVE_SECONDS})}",
        1,
    )

    reset_mutation_anchor = (
        "function d6r(){let e=(0,$F.c)(3),t=lt(),n=zO(),r;return "
        "e[0]!==n||e[1]!==t?(r={mutationFn:f6r,onSuccess:(e,r)=>{"
        "let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){"
        "let n=e.code===`reset`?e.credit?.id??i:i;"
        "t.setQueryData([`rate-limit-reset-credits`],e=>F3r(e,a,n))}"
        "Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},"
        "e[0]=n,e[1]=t,e[2]=r):r=e[2],$t(r)}"
    )
    if bundle.count(reset_mutation_anchor) != 1:
        raise RuntimeError("could not find the native reset-credit mutation")
    bundle = bundle.replace(
        reset_mutation_anchor,
        "function d6r(){let e=lt(),t=zO(),n=window.__codexMuxResetAccountId,"
        "r=[`rate-limit-reset-credits`,n??`primary`];return $t({"
        "mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):f6r,"
        "onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;"
        "if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?"
        "n.credit?.id??a:a;e.setQueryData(r,e=>F3r(e,o,t))}"
        "Promise.all([t([`rate-limit-status`]),t(r)])}})}",
        1,
    )

    selected_usage_anchor = "let y=v;if(g!=null){"
    if bundle.count(selected_usage_anchor) != 1:
        raise RuntimeError("could not find the native usage-window selection")
    bundle = bundle.replace(
        selected_usage_anchor,
        "let y=window.__codexMuxSelectedUsageWindows??v;if(g!=null){",
        1,
    )

    usage_header_anchor = (
        "let ve;t[46]===ge?ve=t[47]:"
        "(ve=(0,k2.jsxs)(LL,{children:[ge,_e]}),t[46]=ge,t[47]=ve);"
    )
    if bundle.count(usage_header_anchor) != 1:
        raise RuntimeError("could not find the native Usage sheet header")
    bundle = bundle.replace(
        usage_header_anchor,
        "let ve=(0,k2.jsxs)(LL,{children:[ge,_e,"
        "window.__codexMuxResetAccountSelector??null]});",
        1,
    )

    usage_anchor = "usageItems:Ge"
    if bundle.count(usage_anchor) != 1:
        raise RuntimeError("could not find the native ChatGPT usage menu slot")
    bundle = bundle.replace(
        usage_anchor,
        "usageItems:(0,e7.jsx)(CodexMuxAccountMenu,{})",
        1,
    )

    open_change_anchors = (
        "triggerButton:Ke,onOpenChange:o,children:(0,e7.jsx)(bXc",
        "return(0,e7.jsx)(vH,{open:a,onOpenChange:o,contentWidth:`panel`",
    )
    for anchor in open_change_anchors:
        if bundle.count(anchor) != 1:
            raise RuntimeError("could not find a native profile menu open-state hook")
        bundle = bundle.replace(
            anchor,
            anchor.replace(
                "onOpenChange:o",
                "onOpenChange:CodexMuxProfileMenuOpenChange(o)",
            ),
            1,
        )

    depleted_alert_anchors = (
        "defaultMessage:`You’re out of Codex and Work usage`",
        "defaultMessage:`You’ve used all Codex and Work usage`",
        "defaultMessage:`You’ve reached your usage limit`",
    )
    for depleted_anchor in depleted_alert_anchors:
        if bundle.count(depleted_anchor) != 1:
            raise RuntimeError("could not find a native subscription depletion alert")
        bundle = bundle.replace(
            depleted_anchor,
            "defaultMessage:`All connected subscriptions are depleted`",
            1,
        )
    bundle_path.write_text(bundle, encoding="utf-8")

    profile_bundles = list((webview / "assets").glob("profile-*.js"))
    if len(profile_bundles) != 1:
        raise RuntimeError(
            f"expected one native Profile settings bundle, found {len(profile_bundles)}"
        )
    profile_bundle_path = profile_bundles[0]
    profile_bundle = profile_bundle_path.read_text(encoding="utf-8")
    profile_avatar_anchor = (
        "children:[(0,$.jsxs)(`div`,{className:`relative mb-4 size-20`,children:["
    )
    if profile_bundle.count(profile_avatar_anchor) != 1:
        raise RuntimeError("could not find the native Profile avatar")
    profile_bundle = profile_bundle.replace(
        profile_avatar_anchor,
        "children:[globalThis.CodexMuxProfileAvatarStack?.("
        "{onSelect:()=>A.refetch()})??null,"
        "(0,$.jsxs)(`div`,{className:"
        "globalThis.CodexMuxProfileAvatarStack?"
        "`hidden`:`relative mb-4 size-20`,children:[",
        1,
    )

    profile_name_anchor = "className:`flex w-full justify-center`"
    if profile_bundle.count(profile_name_anchor) != 1:
        raise RuntimeError("could not find the native Profile display name")
    profile_bundle = profile_bundle.replace(
        profile_name_anchor,
        "className:globalThis.__codexMuxSelectedProfileAccountId&&!A.isFetching?"
        "`flex w-full justify-center`:`hidden`",
        1,
    )
    profile_identity_anchor = (
        "className:`mt-1 flex min-h-7 items-center gap-1.5 text-base leading-5 "
        "font-normal text-token-text-tertiary`"
    )
    if profile_bundle.count(profile_identity_anchor) != 1:
        raise RuntimeError("could not find the native Profile username and plan badge")
    profile_bundle = profile_bundle.replace(
        profile_identity_anchor,
        "className:globalThis.__codexMuxSelectedProfileAccountId&&!A.isFetching?"
        "`mt-1 flex min-h-7 items-center gap-1.5 text-base leading-5 font-normal "
        "text-token-text-tertiary`:`hidden`",
        1,
    )
    profile_bundle_path.write_text(profile_bundle, encoding="utf-8")

    plugin_scope_anchor = "action:F,children:w})"
    plugin_bundles = [
        path
        for path in (webview / "assets").glob("plugins-settings-*.js")
        if plugin_scope_anchor in path.read_text(encoding="utf-8")
    ]
    if len(plugin_bundles) != 1:
        raise RuntimeError(
            f"expected one native Plugins settings bundle, found {len(plugin_bundles)}"
        )
    plugin_bundle_path = plugin_bundles[0]
    plugin_bundle = plugin_bundle_path.read_text(encoding="utf-8")
    if plugin_bundle.count(plugin_scope_anchor) != 1:
        raise RuntimeError("could not find the native Plugins settings content")
    plugin_bundle = plugin_bundle.replace(
        plugin_scope_anchor,
        "action:F,children:[globalThis.CodexMuxPluginScope?.()??null,w]})",
        1,
    )
    plugin_bundle_path.write_text(plugin_bundle, encoding="utf-8")

    thread_bundles = list((webview / "assets").glob("local-conversation-thread-*.js"))
    if len(thread_bundles) != 1:
        raise RuntimeError(
            f"expected one local conversation renderer bundle, found {len(thread_bundles)}"
        )
    thread_bundle_path = thread_bundles[0]
    thread_bundle = thread_bundle_path.read_text(encoding="utf-8")
    thread_component = (PROJECT_ROOT / "ui" / "thread-subscription.js").read_text(
        encoding="utf-8"
    )
    thread_component = thread_component.replace(
        "__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT)
    )
    thread_component = thread_component.replace("__CODEX_MUX_CONTROL_TOKEN__", token)
    thread_component_anchor = "function bE(){let e=(0,wE.c)(57)"
    if thread_bundle.count(thread_component_anchor) != 1:
        raise RuntimeError("could not find the native thread summary sources component")
    thread_bundle = thread_bundle.replace(
        thread_component_anchor,
        thread_component + "\n" + thread_component_anchor,
        1,
    )
    summary_children_anchor = "children:[c,l,u,d,f,p,m,h,g,_,v,y,b,x]"
    if thread_bundle.count(summary_children_anchor) != 1:
        raise RuntimeError("could not find the native thread summary section list")
    thread_bundle = thread_bundle.replace(
        summary_children_anchor,
        "children:[c,l,u,d,f,(0,zE.jsx)(CodexMuxThreadSubscription,{}),p,m,h,g,_,v,y,b,x]",
        1,
    )
    thread_bundle_path.write_text(thread_bundle, encoding="utf-8")


    thread_bundle_path.write_text(thread_bundle, encoding="utf-8")


# --- Build-8378 (Intel x86_64, split renderer) anchor set -------------------
# Every anchor below was counted (count == 1) on ChatGPT 26.903.61454/8378.
# See docs/INTEL-RENDERER-8378.md for the semantic mapping from build 6396.

ACCOUNT_MENU_8378_PRELUDE_MARKER = "function CodexMuxUsageModal"

ACCOUNT_MENU_8378_RENAMES: tuple[tuple[str, str, int], ...] = (
    ("e7", "dq", 55),
    ("kXc", "Pyn", 27),
    ("QLs", "tG", 1),
    ("CH", "of", 2),
    ("_H", "Qy", 5),
    ("S2", "MG", 2),
    ("BW", "Zv", 1),
    ("jLa", "uB", 1),
    ("lt", "vr", 3),
)

ACCOUNT_MENU_8378_REMNANTS: tuple[str, ...] = (
    "e7",
    "kXc",
    "QLs",
    "CH",
    "_H",
    "S2",
    "BW",
    "jLa",
)

ACCOUNT_MENU_8378_UNVERIFIED_PROPS_ANCHOR = (
    '          tone: "danger",\n'
    "          allowWrap: true,\n"
    "          subTextAllowWrap: true,\n"
)

THREAD_SUBSCRIPTION_8378_RENAMES: tuple[tuple[str, str, int], ...] = (
    ("zE", "cE", 6),
    ("TE", "Uk", 2),
    ("K", "Z", 1),
)


def app_initial_prelude_8378(token: str) -> str:
    """Extract the self-contained request helpers for the app-initial bundle.

    The prelude is the top of ui/account-menu.js (control API client, plugin
    scope marker, profile/reset fetchers). It must reference no upstream
    bundle identifiers so it can be prepended to another module safely.
    """
    source = (PROJECT_ROOT / "ui" / "account-menu.js").read_text(encoding="utf-8")
    if ACCOUNT_MENU_8378_PRELUDE_MARKER not in source:
        raise RuntimeError("could not locate the account-menu prelude boundary")
    prelude = source.split(ACCOUNT_MENU_8378_PRELUDE_MARKER)[0]
    for helper in (
        "codexMuxScopePluginRequest",
        "codexMuxRequest",
        "codexMuxProfileData",
        "codexMuxRateLimitResets",
        "codexMuxConsumeRateLimitReset",
        "codexMuxConsumeResetForAccount",
    ):
        if f"function {helper}" not in prelude and f"async function {helper}" not in prelude:
            raise RuntimeError(
                f"the account-menu prelude no longer defines {helper}; "
                "review the app-initial injection"
            )
    for upstream in (
        "e7",
        "kXc",
        "QLs",
        "CH",
        "_H",
        "S2",
        "BW",
        "jLa",
        "lt",
        "Pyn",
        "Qy",
    ):
        if re.search(rf"(?<![\w$]){re.escape(upstream)}(?![\w$])", prelude):
            raise RuntimeError(
                f"the account-menu prelude must stay free of upstream "
                f"identifiers, but references {upstream!r}"
            )
    prelude = prelude.replace("__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT))
    return prelude.replace("__CODEX_MUX_CONTROL_TOKEN__", token)


def transform_account_menu_8378(token: str) -> str:
    """Adapt the account menu component to the 8378 app-primary scope."""
    component = (PROJECT_ROOT / "ui" / "account-menu.js").read_text(encoding="utf-8")
    component = component.replace("__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT))
    component = component.replace("__CODEX_MUX_CONTROL_TOKEN__", token)
    for old, new, expected in ACCOUNT_MENU_8378_RENAMES:
        component = replace_identifier(component, old, new, expected, f"8378 {old!r}")
    scope_anchor = "const modalScope = Lo(Q);"
    component = replace_unique(
        component, scope_anchor, "const modalScope = fo(HE);", "the 8378 modal scope"
    )
    component = replace_unique(
        component,
        ACCOUNT_MENU_8378_UNVERIFIED_PROPS_ANCHOR,
        "",
        "the unverified 8378 menu-row props",
    )
    for remnant in ACCOUNT_MENU_8378_REMNANTS:
        if re.search(rf"(?<![\w$]){re.escape(remnant)}(?![\w$])", component):
            raise RuntimeError(
                f"the transformed 8378 account menu still references {remnant!r}"
            )
    if re.search(r"(?<![\w$])Lo\(|\bQ\)", component):
        raise RuntimeError(
            "the transformed 8378 account menu still references the legacy modal scope"
        )
    return component


def transform_thread_subscription_8378(token: str) -> str:
    """Adapt the thread subscription indicator to the 8378 thread chunk."""
    component = (PROJECT_ROOT / "ui" / "thread-subscription.js").read_text(
        encoding="utf-8"
    )
    component = component.replace("__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT))
    component = component.replace("__CODEX_MUX_CONTROL_TOKEN__", token)
    component = replace_unique(
        component,
        "$n(sr)",
        "ve(ds)",
        "the 8378 thread route hook",
    )
    for old, new, expected in THREAD_SUBSCRIPTION_8378_RENAMES:
        component = replace_identifier(component, old, new, expected, f"8378 {old!r}")
    for remnant in ("$n", "sr", "zE", "TE"):
        if re.search(rf"(?<![\w$]){re.escape(remnant)}(?![\w$])", component):
            raise RuntimeError(
                f"the transformed 8378 thread component still references {remnant!r}"
            )
    return component


def patch_app_initial_8378(extracted: Path, token: str) -> None:
    """Patch the 8378 RPC/query bundle (app-initial)."""
    webview = extracted / "webview"
    initial_bundles = list((webview / "assets").glob("app-initial-*.js"))
    if len(initial_bundles) != 1:
        raise RuntimeError(
            f"expected one ChatGPT initial renderer bundle, found {len(initial_bundles)}"
        )
    bundle_path = initial_bundles[0]
    bundle = bundle_path.read_text(encoding="utf-8")
    if "function CodexMuxAccountMenu(" in bundle or "codexMuxScopePluginRequest" in bundle:
        raise RuntimeError("source app already contains the Codex multiplexer hooks")

    # Fail-closed verification that the plugin RPC wiring is the reviewed one.
    for anchor, description in (
        ('"app/list"', "the app/list RPC"),
        ('"app/installed"', "the app/installed RPC"),
        ('"app/read"', "the app/read RPC"),
        ('"mcpServer/oauth/login":`mcp`', "the MCP OAuth login RPC mapping"),
        ('"mcpServerStatus/list":`mcp`', "the MCP status RPC mapping"),
        (
            "listMcpServers(e,t){let n=JSON.stringify({options:t,params:e})",
            "the MCP status request method",
        ),
    ):
        require_unique(bundle, anchor, description)

    prelude = app_initial_prelude_8378(token)
    bundle = prelude + "\n" + bundle

    bridge_anchor = (
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)"
        "throw Error(`AppServerRequestClient is missing a message dispatcher`);"
        "return e===`config/read`?this.sendConfigReadRequest(t,n):"
        "this.enqueueRequest(e,t,"
        "e===`plugin/list`&&n?.timeoutMs==null?{...n,timeoutMs:cwn}:n)}"
    )
    bundle = replace_unique(
        bundle,
        bridge_anchor,
        bridge_anchor.replace(
            "this.enqueueRequest(e,t,",
            "this.enqueueRequest(e,codexMuxScopePluginRequest(e,t),",
            1,
        ),
        "the native app-server request bridge",
    )

    profile_query_anchor = "let e=await vO.safeGet(`/wham/profiles/me`)"
    bundle = replace_unique(
        bundle,
        profile_query_anchor,
        "let e=await codexMuxProfileData("
        "globalThis.__codexMuxSelectedProfileAccountId??null)",
        "the native profile stats request",
    )

    reset_query_key_anchor = "t={queryKey:[`rate-limit-reset-credits`],queryFn:C5i,"
    # The account id is read from the query key itself, not from the mutable
    # selection global: key and fetch are evaluated at different times, and a
    # background refetch racing an account switch would otherwise cache one
    # subscription's credits under the other's key.
    bundle = replace_unique(
        bundle,
        reset_query_key_anchor,
        "t={queryKey:[`rate-limit-reset-credits`,"
        "window.__codexMuxResetAccountId??`primary`],"
        "queryFn:({queryKey:e})=>e[1]!==`primary`?"
        "codexMuxRateLimitResets(e[1]):C5i,",
        "the native reset-credit query",
    )

    reset_mutation_anchor = "r={mutationFn:T5i,"
    bundle = replace_unique(
        bundle,
        reset_mutation_anchor,
        "r={mutationFn:window.__codexMuxResetAccountId!=null?"
        "e=>codexMuxConsumeResetForAccount(window.__codexMuxResetAccountId,e,t):T5i,",
        "the native reset-credit mutation",
    )
    reset_key_suffix = ",window.__codexMuxResetAccountId??`primary`"
    bundle = replace_unique(
        bundle,
        "t.setQueryData([`rate-limit-reset-credits`],e=>s8i(e,a,n))",
        "t.setQueryData([`rate-limit-reset-credits`"
        f"{reset_key_suffix}],e=>s8i(e,a,n))",
        "the native reset-credit mutation cache update",
    )
    bundle = replace_unique(
        bundle,
        "Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])",
        "Promise.all([n([`rate-limit-status`]),"
        "n([`rate-limit-reset-credits`"
        f"{reset_key_suffix}])])",
        "the native reset-credit mutation invalidation",
    )

    bundle_path.write_text(bundle, encoding="utf-8")
    check_js_module_syntax(bundle_path)


def patch_app_primary_8378(extracted: Path, token: str) -> None:
    """Patch the 8378 sidebar/menu/modal bundle (app-primary)."""
    webview = extracted / "webview"
    primary_bundles = list((webview / "assets").glob("app-primary-*.js"))
    if len(primary_bundles) != 1:
        raise RuntimeError(
            f"expected one ChatGPT primary renderer bundle, found {len(primary_bundles)}"
        )
    bundle_path = primary_bundles[0]
    bundle = bundle_path.read_text(encoding="utf-8")
    if "function CodexMuxAccountMenu(" in bundle:
        raise RuntimeError("source app already contains the Codex multiplexer menu")

    component = transform_account_menu_8378(token)
    component_anchor = (
        "function kyn(e){let t=(0,uq.c)(223),{sidebarFooter:n,triggerButton:r}=e"
    )
    bundle = replace_unique(
        bundle,
        component_anchor,
        component + "\n" + component_anchor,
        "the native ChatGPT profile menu component",
    )

    bundle = replace_unique(
        bundle,
        "usageItems:wt",
        "usageItems:(0,dq.jsx)(CodexMuxAccountMenu,{})",
        "the native ChatGPT usage menu slot",
    )

    open_change_anchor = "triggerButton:Ot,onOpenChange:c,children:[F,null]"
    bundle = replace_unique(
        bundle,
        open_change_anchor,
        open_change_anchor.replace(
            "onOpenChange:c",
            "onOpenChange:CodexMuxProfileMenuOpenChange(c)",
            1,
        ),
        "the native profile menu open-state hook",
    )

    bundle = replace_unique(
        bundle,
        "function Adn(e){",
        "function Adn(e){CodexMuxUseResetAccountState();",
        "the native Usage modal component",
    )

    bundle = replace_unique(
        bundle,
        "F=D!=null&&T!==`idle`&&!(T===`awaiting-refresh`&&N)?D:j",
        "F=window.__codexMuxSelectedUsageWindows??"
        "(D!=null&&T!==`idle`&&!(T===`awaiting-refresh`&&N)?D:j)",
        "the native usage-window selection",
    )

    bundle = replace_unique(
        bundle,
        "(ge=(0,eG.jsxs)(Eb,{children:[me,he]}",
        "(ge=(0,eG.jsxs)(Eb,{children:[me,he,"
        "window.__codexMuxResetAccountSelector??null]}",
        "the native Usage sheet header",
    )

    for depleted_anchor in (
        "defaultMessage:`You’re out of Codex and Work usage`",
        "defaultMessage:`You’ve used all Codex and Work usage`",
        "defaultMessage:`You’ve reached your usage limit`",
    ):
        bundle = replace_unique(
            bundle,
            depleted_anchor,
            "defaultMessage:`All connected subscriptions are depleted`",
            "a native subscription depletion alert",
        )

    bundle_path.write_text(bundle, encoding="utf-8")
    check_js_module_syntax(bundle_path)


def patch_profile_8378(extracted: Path) -> None:
    """Patch the 8378 Profile settings bundle."""
    webview = extracted / "webview"
    candidates = [
        path
        for path in (webview / "assets").glob("profile-*.js")
        if "avatar:(0,$.jsxs)($.Fragment,{children:[(0,$.jsxs)(`label`,"
        in path.read_text(encoding="utf-8")
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one native Profile settings bundle, found {len(candidates)}"
        )
    bundle_path = candidates[0]
    bundle = bundle_path.read_text(encoding="utf-8")
    if "CodexMuxProfileAvatarStack" in bundle:
        raise RuntimeError("source app already contains the Codex profile stack")

    avatar_anchor = "avatar:(0,$.jsxs)($.Fragment,{children:[(0,$.jsxs)(`label`,"
    bundle = replace_unique(
        bundle,
        avatar_anchor,
        "avatar:(0,$.jsxs)($.Fragment,{children:["
        "globalThis.CodexMuxProfileAvatarStack?.({})??null,"
        "(0,$.jsxs)(`label`,",
        "the native Profile avatar",
    )

    label_class_anchor = "className:ue(`group relative flex size-20"
    bundle = replace_unique(
        bundle,
        label_class_anchor,
        "className:globalThis.CodexMuxProfileAvatarStack?`hidden`:ue(`group relative flex size-20",
        "the native Profile avatar label",
    )

    bundle_path.write_text(bundle, encoding="utf-8")
    check_js_module_syntax(bundle_path)


def patch_plugins_8378(extracted: Path) -> None:
    """Patch the 8378 Plugins settings bundle."""
    webview = extracted / "webview"
    plugin_scope_anchor = "action:F,children:w})"
    plugin_bundles = [
        path
        for path in (webview / "assets").glob("plugins-settings-*.js")
        if plugin_scope_anchor in path.read_text(encoding="utf-8")
    ]
    if len(plugin_bundles) != 1:
        raise RuntimeError(
            f"expected one native Plugins settings bundle, found {len(plugin_bundles)}"
        )
    plugin_bundle_path = plugin_bundles[0]
    plugin_bundle = plugin_bundle_path.read_text(encoding="utf-8")
    bundle = replace_unique(
        plugin_bundle,
        plugin_scope_anchor,
        "action:F,children:[globalThis.CodexMuxPluginScope?.()??null,w]})",
        "the native Plugins settings content",
    )
    plugin_bundle_path.write_text(bundle, encoding="utf-8")
    check_js_module_syntax(plugin_bundle_path)


def patch_thread_8378(extracted: Path, token: str) -> None:
    """Patch the 8378 local conversation thread bundle."""
    webview = extracted / "webview"
    thread_component_anchor = "function iE(e){let t=(0,sE.c)(37)"
    thread_bundles = [
        path
        for path in (webview / "assets").glob("local-conversation-thread-*.js")
        if thread_component_anchor in path.read_text(encoding="utf-8")
    ]
    if len(thread_bundles) != 1:
        raise RuntimeError(
            "expected one local conversation renderer bundle, "
            f"found {len(thread_bundles)}"
        )
    thread_bundle_path = thread_bundles[0]
    thread_bundle = thread_bundle_path.read_text(encoding="utf-8")
    if "function CodexMuxThreadSubscription(" in thread_bundle:
        raise RuntimeError("source app already contains the Codex thread indicator")

    thread_component = transform_thread_subscription_8378(token)
    thread_bundle = replace_unique(
        thread_bundle,
        thread_component_anchor,
        thread_component + "\n" + thread_component_anchor,
        "the native thread summary sources component",
    )

    summary_children_anchor = "children:[m,h,g,_,v,y,b,x]"
    thread_bundle = replace_unique(
        thread_bundle,
        summary_children_anchor,
        "children:[m,h,g,_,v,(0,cE.jsx)(CodexMuxThreadSubscription,{}),y,b,x]",
        "the native thread summary section list",
    )
    thread_bundle_path.write_text(thread_bundle, encoding="utf-8")
    check_js_module_syntax(thread_bundle_path)


def patch_renderer_8378(extracted: Path, token: str) -> None:
    """Apply the build-8378 split-bundle anchor set (Intel Core)."""
    patch_app_initial_8378(extracted, token)
    patch_app_primary_8378(extracted, token)
    patch_profile_8378(extracted)
    patch_plugins_8378(extracted)
    patch_thread_8378(extracted, token)


def patch_desktop_profile(
    extracted: Path,
    installed_computer_use_app: Path | None = None,
    computer_use_enabled: bool = True,
) -> None:
    """Give the copied Electron app its own user-data and single-instance scope.

    In Core mode (``computer_use_enabled=False``) the Computer Use helper is
    absent, so no helper paths are injected and no managed-service pin is
    applied; the copied app must still receive an isolated Chromium profile
    and a disabled updater.
    """
    if computer_use_enabled and installed_computer_use_app is None:
        raise RuntimeError(
            "a Computer Use build requires the installed helper path"
        )
    bootstrap_files = list((extracted / ".vite" / "build").glob("bootstrap-*.js"))
    if len(bootstrap_files) != 1:
        raise RuntimeError(
            f"expected one ChatGPT bootstrap bundle, found {len(bootstrap_files)}"
        )

    bootstrap_path = bootstrap_files[0]
    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    profile_pattern = re.compile(
        r"(?P<electron>[A-Za-z_$][\w$]*)\.app\.setPath\("
        r"`userData`,[A-Za-z_$][\w$]*\(\{"
        r"appDataPath:(?P=electron)\.app\.getPath\(`appData`\),"
        r"buildFlavor:[^,}]+,env:process\.env\}\)\)"
    )

    def replacement(match: re.Match[str]) -> str:
        electron = match.group("electron")
        isolated_profile = (
            f"{electron}.app.setPath(`userData`,"
            f"{electron}.app.getPath(`appData`)+`/{DESKTOP_PROFILE_NAME}`)"
        )
        if not computer_use_enabled:
            # Keep the canonical-refresh skip so a Core build without a helper
            # never downloads or installs the official Computer Use service,
            # but do not point anything at a helper that does not exist.
            return (
                "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=`1`;"
                f"{isolated_profile}"
            )
        assert installed_computer_use_app is not None
        computer_use_pipe = json.dumps(str(DEFAULT_STATE_ROOT / "computer-use.sock"))
        computer_use_app = json.dumps(str(installed_computer_use_app))
        return (
            f"process.env.SKY_CUA_SERVICE_NATIVE_PIPE_PATH={computer_use_pipe};"
            f"process.env.SKY_CUA_SERVICE_PATH={computer_use_app};"
            f"process.env.CODEX_ELECTRON_COMPUTER_USE_APP_PATH={computer_use_app};"
            "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=`1`;"
            f"{isolated_profile}"
        )

    bootstrap, replacements = profile_pattern.subn(replacement, bootstrap, count=1)
    if replacements != 1:
        raise RuntimeError("could not isolate the copied ChatGPT desktop profile")

    # The copied app must never replace itself with an unpatched official update.
    # The updater manager call kept its shape across the bootstrap refactor:
    # older builds chain `try{let{runMainAppStartup:` directly, newer builds
    # import the main bundle first (`let{runMainAppStartup:`). Either shape must
    # match exactly once; anything else stays fail-closed.
    updater_replacements = 0
    for updater_pattern in (
        re.compile(
            r"await [A-Za-z_$][\w$]*\.initialize\(\);"
            r"(?=try\{let\{runMainAppStartup:)"
        ),
        re.compile(
            r"await [A-Za-z_$][\w$]*\.initialize\(\);"
            r"(?=let\{runMainAppStartup:)"
        ),
    ):
        bootstrap, updater_replacements = updater_pattern.subn("", bootstrap, count=1)
        if updater_replacements == 1:
            break
    if updater_replacements != 1:
        raise RuntimeError("could not disable updates in the copied ChatGPT app")
    bootstrap_path.write_text(bootstrap, encoding="utf-8")

    main_files = list((extracted / ".vite" / "build").glob("main-*.js"))
    if len(main_files) != 1:
        raise RuntimeError(
            f"expected one ChatGPT desktop main bundle, found {len(main_files)}"
        )
    main_path = main_files[0]
    main = main_path.read_text(encoding="utf-8")
    if computer_use_enabled:
        assert installed_computer_use_app is not None
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

    computer_use_instruction = (
        "Control desktop apps on macOS through Computer Use."
    )
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
    ui_test_bridge = extracted / ".vite" / "build" / "ui-test-bridge.cjs"
    shutil.copy2(PROJECT_ROOT / "ui" / "ui-test-bridge.cjs", ui_test_bridge)
    main += (
        "\n;if(process.env.CODEX_MUX_UI_TESTS===`1`)"
        "require(require(`node:path`).join(__dirname,`ui-test-bridge.cjs`)).start();"
    )
    main_path.write_text(main, encoding="utf-8")


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
    digest = hashlib.sha256(asar_path.read_bytes()).hexdigest()
    info["ElectronAsarIntegrity"] = {
        "Resources/app.asar": {"algorithm": "SHA256", "hash": digest}
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)


def unregister_launch_services(path: Path) -> None:
    """Remove a retired helper from the Launch Services database."""
    if LAUNCH_SERVICES_REGISTER.is_file() and path.exists():
        run([str(LAUNCH_SERVICES_REGISTER), "-u", str(path)])


def patch_app(
    source: Path,
    destination: Path,
    force: bool,
    allow_adhoc_signing: bool,
    allow_untested_source: bool,
    allow_signing_team_change: bool,
    enable_computer_use: bool = False,
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
    source_architecture = detect_source_architecture(source)
    compatibility = lookup_source_compatibility(
        source_version, source_build, source_architecture, source_asar_hash
    )
    if compatibility is None:
        print(
            f"Source ChatGPT version: {source_version} ({source_build}), "
            f"architecture: {source_architecture}, "
            f"app.asar {source_asar_hash}"
        )
    else:
        print(
            f"Source ChatGPT version: {source_version} ({source_build}), "
            f"architecture: {compatibility.architecture}, "
            f"app.asar {source_asar_hash}"
        )
    if compatibility is None and not allow_untested_source:
        raise RuntimeError(
            "the source version, build, architecture, or app.asar hash is not approved; "
            "review the upstream change or pass --allow-untested-source"
        )
    if compatibility is None:
        print(
            "Warning: continuing with an untested official ChatGPT build; "
            "the patch will continue only while every expected anchor matches.",
            file=sys.stderr,
        )
        computer_use_enabled = enable_computer_use
        expected_cua_replacements = EXPECTED_CUA_IDENTIFIER_REPLACEMENTS
        expected_asar_cua_replacements = EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS
        if computer_use_enabled:
            print(
                "Warning: attempting an untested full build with Computer Use; "
                "every binary occurrence check stays fail-closed.",
                file=sys.stderr,
            )
    else:
        computer_use_enabled = compatibility.computer_use_supported
        expected_cua_replacements = compatibility.cua_identifier_replacements
        expected_asar_cua_replacements = compatibility.asar_cua_identifier_replacements
    renderer = lookup_renderer_compatibility(
        source_version, source_build, source_architecture
    )
    if renderer is not None:
        print(
            f"Renderer anchor set: build {renderer.build} "
            f"({renderer.architecture}, {renderer.layout} layout)"
        )
    else:
        print(
            "Renderer anchor set: legacy build-6396 anchors "
            "(fail-closed on unfamiliar bundles)"
        )
    if computer_use_enabled:
        print("Capability level: full (independent Computer Use helper)")
    else:
        print(
            "Capability level: core (app, routing, profiles, and plugins "
            "without the Computer Use helper)"
        )

    for tool in ("codesign", "ditto", "go", "node", "npm", "security", "xcrun"):
        require_tool(tool)
    asar = ensure_asar_tool()
    token = load_or_create_token()
    signing_identity = resolve_signing_identity(allow_adhoc_signing)
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

        print(f"Building multiplexer for {source_architecture}…")
        build_proxy(proxy, source_architecture)
        print("Copying ChatGPT.app…")
        run(["ditto", str(source), str(staged_app)])
        install_launcher(staged_app, source_architecture)

        resources = staged_app / "Contents" / "Resources"
        original_asar = resources / "app.asar"
        print("Patching desktop profile and renderer…")
        run([str(asar), "extract", str(original_asar), str(extracted)])
        if computer_use_enabled:
            patch_asar_computer_use_identity(
                extracted, expected_replacements=expected_asar_cua_replacements
            )
            patch_desktop_profile(
                extracted,
                installed_computer_use_app,
                computer_use_enabled=True,
            )
        else:
            patch_desktop_profile(
                extracted,
                installed_computer_use_app=None,
                computer_use_enabled=False,
            )
        patch_renderer(extracted, token, renderer)
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

        bundled_codex = resources / "codex"
        real_codex = resources / "codex.real"
        if real_codex.exists():
            raise RuntimeError("source app already contains codex.real")
        bundled_codex.rename(real_codex)
        shutil.copy2(proxy, bundled_codex)
        bundled_codex.chmod(0o755)

        # codesign never converts one CPU architecture into another, so every
        # native binary in the staged tree must already carry the target slice.
        print(f"Validating staged native code for {source_architecture}…")
        checked_binaries = validate_macho_tree_for_architecture(
            staged_app, source_architecture
        )
        print(f"Validated {len(checked_binaries)} native binaries for {source_architecture}")

        patch_info_plist(staged_app, original_asar, team_identifier)
        print(f"Signing independent app copy with {signing_identity}…")
        sign_independent_app(
            staged_app,
            signing_identity,
            team_identifier,
            architecture=source_architecture,
            expected_cua_identifier_replacements=expected_cua_replacements,
            computer_use_enabled=computer_use_enabled,
        )
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
        if computer_use_enabled:
            if not bundled_computer_use_app.is_dir():
                raise RuntimeError(
                    "a full build requires the bundled Codex Computer Use service, "
                    "but the source app does not contain one; rebuild in Core mode"
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
                if computer_use_enabled:
                    installed_computer_use_app.rename(helper_backup)
                    print(f"Existing Computer Use helper moved to {helper_backup}")
                else:
                    # A Core build must not leave a stale helper behind: it would
                    # advertise Computer Use support that this build cannot provide.
                    unregister_launch_services(installed_computer_use_app)
                    installed_computer_use_app.rename(helper_backup)
                    print(
                        "Existing Computer Use helper retired to "
                        f"{helper_backup} (Core build has no helper)"
                    )
            staged_app.rename(destination)
            if computer_use_enabled:
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
        registered = [str(destination)]
        if computer_use_enabled:
            registered.append(str(installed_computer_use_app))
        run([str(LAUNCH_SERVICES_REGISTER), "-f", *registered])
    retire_stale_cached_computer_use_app()

    # The official source bundle must remain untouched: re-hash its ASAR after
    # the build and refuse to report success on any mismatch.
    post_build_asar_hash = hashlib.sha256(source_asar.read_bytes()).hexdigest()
    if post_build_asar_hash != source_asar_hash:
        raise RuntimeError(
            "the official ChatGPT app.asar changed during the build; "
            "the source bundle must never be modified"
        )

    print(destination)
    if computer_use_enabled:
        print(installed_computer_use_app)
    else:
        print("Core build: no Computer Use helper installed")


def main() -> int:
    args = parse_args()
    if args.print_install_plan:
        try:
            plan = install_plan(args.source, args.enable_computer_use)
        except (RuntimeError, OSError) as error:
            print(f"install plan failed: {error}", file=sys.stderr)
            return 1
        for key in (
            "source",
            "source_version",
            "source_build",
            "architecture",
            "capability",
            "minimum_macos",
        ):
            print(f"{key}={plan[key]}")
        return 0
    try:
        patch_app(
            args.source,
            args.destination,
            args.force,
            args.allow_adhoc_signing,
            args.allow_untested_source,
            args.allow_signing_team_change,
            args.enable_computer_use,
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
