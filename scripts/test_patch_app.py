#!/usr/bin/env python3
"""Unit tests for the architecture-aware patcher (Intel x86_64 port).

These tests never touch /Applications/ChatGPT.app or user state; every
filesystem fixture lives under a temporary directory.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import patch_app
from patch_app import (
    CLANG_ARCH_BY_ARCHITECTURE,
    GOARCH_BY_ARCHITECTURE,
    SUPPORTED_ARCHITECTURES,
    SourceCompatibility,
    arm64_swift_small_string,
    detect_source_architecture,
    goarch_for_architecture,
    clang_arch_for_architecture,
    lookup_source_compatibility,
    macho_architectures,
    patch_arm64_team_identity,
    patch_computer_use_team_identity,
    patch_desktop_profile,
    patch_x86_64_team_identity,
    replace_same_length_identifier,
    validate_macho_tree_for_architecture,
)


def host_arch() -> str:
    return subprocess.check_output(["uname", "-m"], text=True).strip()


class ArchitectureMappingTests(unittest.TestCase):
    def test_supported_architectures(self) -> None:
        self.assertEqual(set(SUPPORTED_ARCHITECTURES), {"arm64", "x86_64"})

    def test_goarch_mapping(self) -> None:
        self.assertEqual(goarch_for_architecture("arm64"), "arm64")
        self.assertEqual(goarch_for_architecture("x86_64"), "amd64")
        self.assertEqual(GOARCH_BY_ARCHITECTURE["x86_64"], "amd64")

    def test_clang_arch_mapping(self) -> None:
        self.assertEqual(clang_arch_for_architecture("arm64"), "arm64")
        self.assertEqual(clang_arch_for_architecture("x86_64"), "x86_64")
        self.assertEqual(CLANG_ARCH_BY_ARCHITECTURE["x86_64"], "x86_64")

    def test_unsupported_architecture(self) -> None:
        for helper in (goarch_for_architecture, clang_arch_for_architecture):
            with self.assertRaises(RuntimeError):
                helper("i386")
        with self.assertRaises(RuntimeError):
            validate_macho_tree_for_architecture(Path("/tmp"), "ppc")


class CompatibilityModelTests(unittest.TestCase):
    def test_arm_profile_is_tested(self) -> None:
        found = lookup_source_compatibility(
            "26.803.61601",
            "6396",
            "arm64",
            "d5a44ed9e2f1db5f81dbbe85408aed256f3203c5b16f00817bb9d7cd941343cf",
        )
        self.assertIsNotNone(found)
        assert found is not None
        self.assertTrue(found.computer_use_supported)
        self.assertEqual(found.architecture, "arm64")

    def test_compatibility_is_architecture_aware(self) -> None:
        # The same version/build/hash on another architecture must not match.
        self.assertIsNone(
            lookup_source_compatibility(
                "26.803.61601",
                "6396",
                "x86_64",
                "d5a44ed9e2f1db5f81dbbe85408aed256f3203c5b16f00817bb9d7cd941343cf",
            )
        )

    def test_unknown_source_has_no_profile(self) -> None:
        self.assertIsNone(
            lookup_source_compatibility("99.0", "1", "x86_64", "0" * 64)
        )

    def test_hash_mismatch_has_no_profile(self) -> None:
        self.assertIsNone(
            lookup_source_compatibility("26.803.61601", "6396", "arm64", "0" * 64)
        )

    def test_profile_is_immutable(self) -> None:
        profile = SourceCompatibility(
            version="1",
            build="2",
            architecture="x86_64",
            asar_sha256="0" * 64,
            computer_use_supported=False,
        )
        with self.assertRaises(AttributeError):
            profile.computer_use_supported = True  # type: ignore[misc]


class MachOTests(unittest.TestCase):
    def test_macho_architectures_of_host_binary(self) -> None:
        slices = macho_architectures(Path("/bin/ls"))
        self.assertIn(host_arch(), slices)

    def test_macho_architectures_rejects_non_macho(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            text = Path(temporary) / "note.txt"
            text.write_text("hello", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                macho_architectures(text)

    def test_validate_tree_accepts_host_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile("/bin/ls", root / "ls-copy")
            (root / "note.txt").write_text("hello", encoding="utf-8")
            checked = validate_macho_tree_for_architecture(root, host_arch())
            self.assertEqual(checked, [root / "ls-copy"])

    def test_validate_tree_rejects_missing_slice(self) -> None:
        other = "arm64" if host_arch() == "x86_64" else "x86_64"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile("/bin/ls", root / "ls-copy")
            with self.assertRaises(RuntimeError):
                validate_macho_tree_for_architecture(root, other)

    def test_validate_tree_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(
                validate_macho_tree_for_architecture(Path(temporary), host_arch()),
                [],
            )


class SourceDetectionTests(unittest.TestCase):
    def _fake_app(self, root: Path, executable_name: str = "ChatGPT") -> Path:
        app = root / "Fake.app"
        macos = app / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        import plistlib

        with (app / "Contents" / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleExecutable": executable_name}, handle)
        shutil.copyfile("/bin/ls", macos / executable_name)
        return app

    def test_detects_host_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = self._fake_app(Path(temporary))
            self.assertEqual(detect_source_architecture(app), host_arch())

    def test_rejects_bundle_without_host_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = self._fake_app(Path(temporary))
            with mock.patch.object(patch_app, "host_architecture", return_value="ppc"):
                with self.assertRaises(RuntimeError):
                    detect_source_architecture(app)

    def test_rejects_non_macho_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "Fake.app"
            macos = app / "Contents" / "MacOS"
            macos.mkdir(parents=True)
            import plistlib

            with (app / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump({"CFBundleExecutable": "ChatGPT"}, handle)
            (macos / "ChatGPT").write_text("not a binary", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                detect_source_architecture(app)


class ComputerUsePatchTests(unittest.TestCase):
    def _synthetic_arm_binary(self, root: Path) -> Path:
        """Build a fake binary with the exact ARM64 occurrence layout."""
        binary = root / "SkyComputerUseService"
        payload = b"header:"
        for team, raw_count in (
            (patch_app.OPENAI_INTERNAL_TEAM_IDENTIFIER, 1),
            (patch_app.OPENAI_DISTRIBUTION_TEAM_IDENTIFIER, 17),
        ):
            payload += arm64_swift_small_string(team) * 2
            payload += team.encode("ascii") * raw_count
        payload += b"com.openai.codex\0:trailer"
        binary.write_bytes(payload)
        return binary

    def test_arm64_small_string_is_deterministic(self) -> None:
        first = arm64_swift_small_string("2DC432GLL2")
        self.assertEqual(len(first), 24)
        self.assertEqual(first, arm64_swift_small_string("2DC432GLL2"))
        self.assertNotEqual(first, arm64_swift_small_string("HX7739G8FX"))
        with self.assertRaises(ValueError):
            arm64_swift_small_string("short")

    def test_arm64_patch_rewrites_exact_occurrences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = self._synthetic_arm_binary(Path(temporary))
            patch_arm64_team_identity(binary, "AAAAAAAAAA")
            patched = binary.read_bytes()
            # Only the raw team constants contain the literal bytes: 1 + 17.
            self.assertEqual(patched.count(b"AAAAAAAAAA"), 1 + 17)
            self.assertEqual(patched.count(b"HX7739G8FX"), 0)
            self.assertEqual(patched.count(b"2DC432GLL2"), 0)
            self.assertEqual(patched.count(b"app.cdxmux.multi\0"), 1)
            self.assertEqual(len(patched), len(binary.read_bytes()))

    def test_arm64_patch_fails_closed_on_bad_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "SkyComputerUseService"
            binary.write_bytes(b"no patterns here")
            with self.assertRaises(RuntimeError):
                patch_arm64_team_identity(binary, "AAAAAAAAAA")

    def test_x86_64_patch_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "SkyComputerUseService"
            binary.write_bytes(b"fake")
            with self.assertRaises(RuntimeError):
                patch_x86_64_team_identity(binary, "AAAAAAAAAA")

    def test_dispatcher_routes_arm64(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = self._synthetic_arm_binary(Path(temporary))
            patch_computer_use_team_identity(binary, "arm64", "AAAAAAAAAA")
            self.assertIn(b"AAAAAAAAAA", binary.read_bytes())

    def test_dispatcher_rejects_x86_64_and_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "SkyComputerUseService"
            binary.write_bytes(b"fake")
            with self.assertRaises(RuntimeError):
                patch_computer_use_team_identity(binary, "x86_64", "AAAAAAAAAA")
            with self.assertRaises(RuntimeError):
                patch_computer_use_team_identity(binary, "ppc", "AAAAAAAAAA")

    def test_replace_requires_same_length(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "blob.bin"
            target.write_bytes(b"abc abc")
            with self.assertRaises(RuntimeError):
                replace_same_length_identifier(target, "abc", "abcd")
            self.assertEqual(replace_same_length_identifier(target, "abc", "xyz"), 2)
            self.assertEqual(target.read_bytes(), b"xyz xyz")


def _write_extracted_fixture(root: Path, updater_shape: str) -> Path:
    extracted = root / "asar"
    build = extracted / ".vite" / "build"
    build.mkdir(parents=True)
    if updater_shape == "legacy":
        updater_call = "await i.initialize();try{let{runMainAppStartup:"
    else:
        updater_call = "await i.initialize();let{runMainAppStartup:"
    bootstrap = (
        "prefix;a.app.setPath(`userData`,w({appDataPath:a.app.getPath(`appData`),"
        "buildFlavor:Z,env:process.env}));middle;"
        f"{updater_call}e}}=await Promise.resolve().then(()=>require(\"./main-abc.js\"));"
    )
    (build / "bootstrap-abc.js").write_text(bootstrap, encoding="utf-8")
    main = (
        "start;ae=new sie(hi(z.codexHome),{onServiceAvailable:e=>{}});"
        "Control desktop apps on macOS through Computer Use.;end"
    )
    (build / "main-abc.js").write_text(main, encoding="utf-8")
    return extracted


class DesktopProfileTests(unittest.TestCase):
    def test_core_mode_has_no_helper_references(self) -> None:
        for shape in ("legacy", "current"):
            with tempfile.TemporaryDirectory() as temporary:
                extracted = _write_extracted_fixture(Path(temporary), shape)
                patch_desktop_profile(
                    extracted,
                    installed_computer_use_app=None,
                    computer_use_enabled=False,
                )
                bootstrap = (extracted / ".vite" / "build" / "bootstrap-abc.js").read_text(
                    encoding="utf-8"
                )
                main = (extracted / ".vite" / "build" / "main-abc.js").read_text(
                    encoding="utf-8"
                )
                self.assertIn("Codex Subscription Router", bootstrap)
                self.assertNotIn("SKY_CUA_SERVICE_PATH", bootstrap)
                self.assertNotIn("SKY_CUA_SERVICE_NATIVE_PIPE_PATH", bootstrap)
                self.assertNotIn("CODEX_ELECTRON_COMPUTER_USE_APP_PATH", bootstrap)
                self.assertIn(
                    "SKIP_COMPUTER_USE_CANONICAL_REFRESH",
                    bootstrap,
                )
                self.assertNotIn(".initialize();", bootstrap)
                # The managed-service pin must be left untouched in Core mode.
                self.assertIn("hi(z.codexHome)", main)
                self.assertIn("report the failure", main)

    def test_full_mode_pins_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extracted = _write_extracted_fixture(Path(temporary), "legacy")
            helper = Path(temporary) / "Codex Subscription Router Computer Use.app"
            patch_desktop_profile(
                extracted,
                installed_computer_use_app=helper,
                computer_use_enabled=True,
            )
            bootstrap = (extracted / ".vite" / "build" / "bootstrap-abc.js").read_text(
                encoding="utf-8"
            )
            main = (extracted / ".vite" / "build" / "main-abc.js").read_text(
                encoding="utf-8"
            )
            self.assertIn("SKY_CUA_SERVICE_PATH", bootstrap)
            self.assertIn(str(helper), main)

    def test_full_mode_requires_helper_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extracted = _write_extracted_fixture(Path(temporary), "legacy")
            with self.assertRaises(RuntimeError):
                patch_desktop_profile(
                    extracted,
                    installed_computer_use_app=None,
                    computer_use_enabled=True,
                )

    def test_updater_must_match_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extracted = _write_extracted_fixture(Path(temporary), "legacy")
            bootstrap_path = extracted / ".vite" / "build" / "bootstrap-abc.js"
            text = bootstrap_path.read_text(encoding="utf-8")
            bootstrap_path.write_text(
                text.replace("await i.initialize();", ""), encoding="utf-8"
            )
            with self.assertRaises(RuntimeError):
                patch_desktop_profile(
                    extracted,
                    installed_computer_use_app=None,
                    computer_use_enabled=False,
                )


class BuildVerificationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("go"), "Go toolchain is required")
    def test_mux_build_targets_host_arch(self) -> None:
        import os

        arch = host_arch()
        if arch not in SUPPORTED_ARCHITECTURES:
            self.skipTest(f"unsupported test host: {arch}")
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "codex-mux"
            environment = dict(os.environ)
            environment["GOOS"] = "darwin"
            environment["GOARCH"] = GOARCH_BY_ARCHITECTURE[arch]
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
                cwd=patch_app.PROJECT_ROOT,
                check=True,
                env=environment,
            )
            self.assertIn(arch, macho_architectures(destination))

    @unittest.skipUnless(
        shutil.which("xcrun") and sys.platform == "darwin",
        "Xcode Command Line Tools are required",
    )
    def test_launcher_build_targets_host_arch(self) -> None:
        arch = host_arch()
        if arch not in SUPPORTED_ARCHITECTURES:
            self.skipTest(f"unsupported test host: {arch}")
        with tempfile.TemporaryDirectory() as temporary:
            launcher = Path(temporary) / "launcher"
            subprocess.run(
                [
                    "xcrun",
                    "clang",
                    "-arch",
                    CLANG_ARCH_BY_ARCHITECTURE[arch],
                    "-Os",
                    "-Wall",
                    "-Wextra",
                    "-o",
                    str(launcher),
                    str(patch_app.PROJECT_ROOT / "native" / "launcher.c"),
                ],
                check=True,
            )
            self.assertIn(arch, macho_architectures(launcher))


if __name__ == "__main__":
    unittest.main()
