#!/usr/bin/env python3
"""Unit tests for the architecture-aware patcher (Intel x86_64 port).

These tests never touch /Applications/ChatGPT.app or user state; every
filesystem fixture lives under a temporary directory.
"""

from __future__ import annotations

import re
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


class RequireUniqueTests(unittest.TestCase):
    def test_positive(self) -> None:
        patch_app.require_unique("ab ab".replace("ab ab", "xay"), "x", "demo")

    def test_zero_matches_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            patch_app.require_unique("hello", "zzz", "demo")

    def test_two_matches_fail(self) -> None:
        with self.assertRaises(RuntimeError):
            patch_app.require_unique("x x", "x", "demo")

    def test_replace_unique(self) -> None:
        self.assertEqual(
            patch_app.replace_unique("aXb", "X", "YZ", "demo"), "aYZb"
        )
        with self.assertRaises(RuntimeError):
            patch_app.replace_unique("aXbXc", "X", "YZ", "demo")

    def test_replace_identifier_ignores_substrings(self) -> None:
        text = "e7 e70 Ge7 e7."
        self.assertEqual(
            patch_app.replace_identifier(text, "e7", "dq", 2, "demo"),
            "dq e70 Ge7 dq.",
        )
        with self.assertRaises(RuntimeError):
            patch_app.replace_identifier(text, "e7", "dq", 3, "demo")


class RendererCompatibilityTests(unittest.TestCase):
    def test_arm_legacy_profile(self) -> None:
        profile = patch_app.lookup_renderer_compatibility(
            "26.803.61601", "6396", "arm64"
        )
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.layout, "legacy")

    def test_intel_split_profile(self) -> None:
        profile = patch_app.lookup_renderer_compatibility(
            "26.903.61454", "8378", "x86_64"
        )
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.layout, "split")

    def test_renderer_lookup_is_architecture_aware(self) -> None:
        self.assertIsNone(
            patch_app.lookup_renderer_compatibility("26.903.61454", "8378", "arm64")
        )
        self.assertIsNone(
            patch_app.lookup_renderer_compatibility("26.803.61601", "6396", "x86_64")
        )

    def test_unknown_renderer_is_none(self) -> None:
        self.assertIsNone(
            patch_app.lookup_renderer_compatibility("99.0", "1", "x86_64")
        )


TEST_TOKEN_8378 = "ab" * 32


class Transform8378Tests(unittest.TestCase):
    def test_prelude_is_self_contained(self) -> None:
        prelude = patch_app.app_initial_prelude_8378(TEST_TOKEN_8378)
        for helper in (
            "codexMuxScopePluginRequest",
            "codexMuxRequest",
            "codexMuxProfileData",
            "codexMuxRateLimitResets",
            "codexMuxConsumeRateLimitReset",
        ):
            self.assertIn(helper, prelude)
        self.assertIn(TEST_TOKEN_8378, prelude)
        self.assertNotIn("__CODEX_MUX_CONTROL_TOKEN__", prelude)
        for upstream in ("e7", "kXc", "QLs", "CH", "Pyn", "Qy", "vr"):
            self.assertIsNone(
                re.search(rf"(?<![\w$]){upstream}(?![\w$])", prelude),
                f"prelude references {upstream}",
            )

    def test_account_menu_transform(self) -> None:
        component = patch_app.transform_account_menu_8378(TEST_TOKEN_8378)
        self.assertIn("function CodexMuxAccountMenu(", component)
        self.assertIn("(0, dq.jsx)(tG,", component)
        self.assertIn("const modalScope = fo(HE);", component)
        self.assertEqual(
            len(re.findall(r"(?<![\w$])Qy(?![\w$])", component)), 5
        )
        self.assertIn("LeftIcon: MG,", component)
        self.assertIn("Zv(modalScope, CodexMuxUsageModal, {})", component)
        self.assertIn("uB(imageUrl || null)", component)
        self.assertIn("const profileQueryClient = vr();", component)
        # Reset selector switches must invalidate native queries at once,
        # otherwise the memoized usage modal shows the previous account.
        self.assertIn("refreshResetQueries", component)
        self.assertIn("rate-limit-reset-credits", component)
        self.assertNotIn('tone: "danger"', component)
        self.assertNotIn("allowWrap", component)
        for remnant in (
            "e7",
            "kXc",
            "QLs",
            "CH",
            "_H",
            "S2",
            "BW",
            "jLa",
        ):
            self.assertIsNone(
                re.search(rf"(?<![\w$]){remnant}(?![\w$])", component),
                f"remnant {remnant}",
            )

    def test_thread_transform(self) -> None:
        component = patch_app.transform_thread_subscription_8378(TEST_TOKEN_8378)
        self.assertIn("function CodexMuxThreadSubscription(", component)
        self.assertIn("const route = ve(ds);", component)
        self.assertIn("Uk.useState", component)
        self.assertIn("Uk.useEffect", component)
        self.assertIn("(0, cE.jsx)(Z.Section,", component)
        for remnant in ("$n", "sr", "zE", "TE"):
            self.assertIsNone(
                re.search(rf"(?<![\w$]){remnant}(?![\w$])", component),
                f"remnant {remnant}",
            )


def _write_assets(root: Path, files: dict[str, str]) -> Path:
    assets = root / "webview" / "assets"
    assets.mkdir(parents=True)
    for name, text in files.items():
        (assets / name).write_text(text, encoding="utf-8")
    (root / "webview" / "index.html").write_text("<html></html>", encoding="utf-8")
    return root


APP_INITIAL_FIXTURE_8378 = """\
export const __fixture = 1;
class AppServerRequestClient {
  async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?this.sendConfigReadRequest(t,n):this.enqueueRequest(e,t,e===`plugin/list`&&n?.timeoutMs==null?{...n,timeoutMs:cwn}:n)}
  enqueueRequest(e,t,n) { return [e, t, n]; }
  sendConfigReadRequest(t,n) { return [t, n]; }
}
const RPC_MAP = {"mcpServer/oauth/login":`mcp`, "mcpServerStatus/list":`mcp`};
const RPC_APPS = ["app/list", "app/installed", "app/read"];
async function pJs(){let e=await vO.safeGet(`/wham/profiles/me`);return e;}
function listMcpServers(e,t){let n=JSON.stringify({options:t,params:e});return n;}
function x5i(){let e=(0,lq.c)(1);dz(),sb(null);let t;return e[0]===Symbol.for(`react.memo_cache_sentinel`)?(t={queryKey:[`rate-limit-reset-credits`],queryFn:C5i,select:S5i,refetchInterval:nD.ONE_MINUTE,staleTime:nD.FIVE_SECONDS},e[0]=t):t=e[0],gb(t)}
function w5i(){let e=(0,lq.c)(3),t=fb(),n=eD(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:T5i,onSuccess:(e,r)=>{let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){let n=e.code===`reset`?e.credit?.id??i:i;t.setQueryData([`rate-limit-reset-credits`],e=>s8i(e,a,n))}Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},e[0]=n,e[1]=t,e[2]=r):r=e[2],yb(r)}
"""

APP_PRIMARY_FIXTURE_8378 = """\
export const __fixture = 1;
function kyn(e){let t=(0,uq.c)(223),{sidebarFooter:n,triggerButton:r}=e;return [n, r];}
const menuProps = {usageItems:wt, other:1};
const dropdown = {triggerButton:Ot,onOpenChange:c,children:[F,null]};
function Adn(e){return e;}
const F=D!=null&&T!==`idle`&&!(T===`awaiting-refresh`&&N)?D:j;
const header=(ge=(0,eG.jsxs)(Eb,{children:[me,he]}));
const d1={defaultMessage:`You’re out of Codex and Work usage`};
const d2={defaultMessage:`You’ve used all Codex and Work usage`};
const d3={defaultMessage:`You’ve reached your usage limit`};
"""

PROFILE_FIXTURE_8378 = (
    "export const x = 1;\n"
    "export const avatar = {avatar:(0,$.jsxs)($.Fragment,{children:[(0,$.jsxs)(`label`,{"
    "className:ue(`group relative flex size-20`,`extra`),children:[]})]})};\n"
)

PLUGINS_FIXTURE_8378 = (
    "export const x = 1;\n"
    "export const panel = (0,D.jsx)(o,{title:O,subtitle:k,action:F,children:w});\n"
)

THREAD_FIXTURE_8378 = (
    "export const x = 1;\n"
    "function iE(e){let t=(0,sE.c)(37);return e;}\n"
    "export const list = {children:[m,h,g,_,v,y,b,x]};\n"
)


class Patch8378FixtureTests(unittest.TestCase):
    def test_app_initial_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_assets(
                Path(temporary), {"app-initial-fixture.js": APP_INITIAL_FIXTURE_8378}
            )
            patch_app.patch_app_initial_8378(root, TEST_TOKEN_8378)
            bundle = (
                root / "webview" / "assets" / "app-initial-fixture.js"
            ).read_text(encoding="utf-8")
            self.assertIn("codexMuxScopePluginRequest(e,t)", bundle)
            self.assertIn("codexMuxProfileData(", bundle)
            self.assertIn("window.__codexMuxResetAccountId", bundle)
            self.assertNotIn("queryFn:C5i,", bundle)
            self.assertNotIn("mutationFn:T5i,", bundle)
            # Already-patched detection is deterministic.
            with self.assertRaises(RuntimeError):
                patch_app.patch_app_initial_8378(root, TEST_TOKEN_8378)

    def test_app_initial_fixture_missing_anchor_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_assets(
                Path(temporary),
                {
                    "app-initial-fixture.js": (
                        "export const x = 1;\n"
                        "async function pJs(){return null;}\n"
                    )
                },
            )
            with self.assertRaises(RuntimeError):
                patch_app.patch_app_initial_8378(root, TEST_TOKEN_8378)

    def test_app_primary_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_assets(
                Path(temporary), {"app-primary-fixture.js": APP_PRIMARY_FIXTURE_8378}
            )
            patch_app.patch_app_primary_8378(root, TEST_TOKEN_8378)
            bundle = (
                root / "webview" / "assets" / "app-primary-fixture.js"
            ).read_text(encoding="utf-8")
            self.assertIn("function CodexMuxAccountMenu(", bundle)
            self.assertIn("usageItems:(0,dq.jsx)(CodexMuxAccountMenu,{})", bundle)
            self.assertIn("CodexMuxProfileMenuOpenChange(c)", bundle)
            self.assertIn("function Adn(e){CodexMuxUseResetAccountState();", bundle)
            self.assertIn("window.__codexMuxSelectedUsageWindows??", bundle)
            self.assertIn("window.__codexMuxResetAccountSelector??null]}", bundle)
            self.assertEqual(
                bundle.count("All connected subscriptions are depleted"), 3
            )
            with self.assertRaises(RuntimeError):
                patch_app.patch_app_primary_8378(root, TEST_TOKEN_8378)

    def test_app_primary_fixture_near_match_fails(self) -> None:
        # A near-match (different memo arity) must not patch.
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_assets(
                Path(temporary),
                {
                    "app-primary-fixture.js": (
                        "export const x = 1;\n"
                        "function kyn(e){let t=(0,uq.c)(224),{sidebarFooter:n,triggerButton:r}=e;return null;}\n"
                    )
                },
            )
            with self.assertRaises(RuntimeError):
                patch_app.patch_app_primary_8378(root, TEST_TOKEN_8378)

    def test_profile_fixture_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_assets(
                Path(temporary),
                {
                    "profile-state-a.js": "export const a = 1;\n",
                    "profile-abc123.js": PROFILE_FIXTURE_8378,
                    "profile-state-b.js": "export const b = 2;\n",
                },
            )
            patch_app.patch_profile_8378(root)
            bundle = (
                root / "webview" / "assets" / "profile-abc123.js"
            ).read_text(encoding="utf-8")
            self.assertIn("globalThis.CodexMuxProfileAvatarStack?.({})??null", bundle)
            self.assertIn("globalThis.CodexMuxProfileAvatarStack?`hidden`", bundle)
            with self.assertRaises(RuntimeError):
                patch_app.patch_profile_8378(root)

    def test_profile_fixture_no_match_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_assets(
                Path(temporary), {"profile-abc123.js": "export const a = 1;\n"}
            )
            with self.assertRaises(RuntimeError):
                patch_app.patch_profile_8378(root)

    def test_plugins_fixture_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_assets(
                Path(temporary),
                {
                    "plugins-settings-a.js": "export const a = 1;\n",
                    "plugins-settings-b.js": PLUGINS_FIXTURE_8378,
                    "plugins-settings-c.js": "export const c = 3;\n",
                },
            )
            patch_app.patch_plugins_8378(root)
            bundle = (
                root / "webview" / "assets" / "plugins-settings-b.js"
            ).read_text(encoding="utf-8")
            self.assertIn("globalThis.CodexMuxPluginScope?.()??null", bundle)

    def test_thread_fixture_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_assets(
                Path(temporary),
                {
                    "local-conversation-thread-a.js": "export const a = 1;\n",
                    "local-conversation-thread-b.js": THREAD_FIXTURE_8378,
                    "local-conversation-thread-c.js": "export const c = 3;\n",
                },
            )
            patch_app.patch_thread_8378(root, TEST_TOKEN_8378)
            bundle = (
                root / "webview" / "assets" / "local-conversation-thread-b.js"
            ).read_text(encoding="utf-8")
            self.assertIn("function CodexMuxThreadSubscription(", bundle)
            self.assertIn("(0,cE.jsx)(CodexMuxThreadSubscription,{})", bundle)
            self.assertNotIn("children:[m,h,g,_,v,y,b,x]", bundle)
            with self.assertRaises(RuntimeError):
                patch_app.patch_thread_8378(root, TEST_TOKEN_8378)

    def test_thread_fixture_two_matches_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_assets(
                Path(temporary),
                {
                    "local-conversation-thread-a.js": THREAD_FIXTURE_8378,
                    "local-conversation-thread-b.js": THREAD_FIXTURE_8378,
                },
            )
            with self.assertRaises(RuntimeError):
                patch_app.patch_thread_8378(root, TEST_TOKEN_8378)


class InstallPolicyTests(unittest.TestCase):
    def test_minimum_macos_matrix(self) -> None:
        self.assertEqual(
            patch_app.minimum_macos_for_capability("x86_64", False), (13, 5)
        )
        self.assertEqual(
            patch_app.minimum_macos_for_capability("x86_64", True), (14, 0)
        )
        self.assertIsNone(patch_app.minimum_macos_for_capability("arm64", False))
        self.assertIsNone(patch_app.minimum_macos_for_capability("arm64", True))
        with self.assertRaises(RuntimeError):
            patch_app.minimum_macos_for_capability("ppc", False)

    def test_minimum_constants_follow_measured_targets(self) -> None:
        # cua Node runtime (minos 13.5) is the highest measured Intel Core
        # deployment target; Full stays at the conservative 14.0 floor.
        self.assertEqual(patch_app.INTEL_CORE_MINIMUM_MACOS, (13, 5))
        self.assertEqual(patch_app.INTEL_FULL_MINIMUM_MACOS, (14, 0))

    def test_plan_for_unknown_source_is_untested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan = patch_app.install_plan(Path(temporary) / "missing.app")
        self.assertEqual(plan["capability"], "untested")
        self.assertEqual(plan["architecture"], "unknown")
        self.assertEqual(plan["minimum_macos"], "none")

    def test_plan_for_unapproved_build_stays_conservative(self) -> None:
        import plistlib

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "Fake.app"
            macos = app / "Contents" / "MacOS"
            macos.mkdir(parents=True)
            with (app / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump(
                    {
                        "CFBundleExecutable": "ChatGPT",
                        "CFBundleShortVersionString": "99.0",
                        "CFBundleVersion": "1",
                    },
                    handle,
                )
            shutil.copyfile("/bin/ls", macos / "ChatGPT")
            (app / "Contents" / "Resources").mkdir(parents=True)
            (app / "Contents" / "Resources" / "app.asar").write_bytes(b"fake")
            plan = patch_app.install_plan(app)
        self.assertEqual(plan["capability"], "untested")
        if host_arch() == "x86_64":
            self.assertEqual(plan["minimum_macos"], "14.0")
        else:
            self.assertEqual(plan["architecture"], host_arch())

    def test_plan_for_tested_intel_core(self) -> None:
        found = patch_app.lookup_source_compatibility(
            "26.903.61454",
            "8378",
            "x86_64",
            "a5f9500e023a65f98dafca3a24a3c0fdf23646caf961b8b3df960d6057e7b450",
        )
        self.assertIsNotNone(found)
        assert found is not None
        self.assertFalse(found.computer_use_supported)
        self.assertEqual(
            patch_app.minimum_macos_for_capability(
                found.architecture, found.computer_use_supported
            ),
            (13, 5),
        )


class UpdateSafetyTests(unittest.TestCase):
    def test_unknown_build_gets_no_renderer_profile(self) -> None:
        self.assertIsNone(
            patch_app.lookup_renderer_compatibility("99.0", "1", "x86_64")
        )
        self.assertIsNone(
            patch_app.lookup_renderer_compatibility("26.903.61454", "8379", "x86_64")
        )

    def test_unknown_build_gets_no_source_profile(self) -> None:
        self.assertIsNone(
            patch_app.lookup_source_compatibility(
                "26.903.61454", "8379", "x86_64", "0" * 64
            )
        )

    def test_legacy_path_fails_closed_on_split_bundle(self) -> None:
        # An unfamiliar (or newer) bundle must not be patched with stale
        # anchors: the legacy path raises instead of partially patching.
        with tempfile.TemporaryDirectory() as temporary:
            root = _write_assets(
                Path(temporary), {"app-initial-fixture.js": APP_PRIMARY_FIXTURE_8378}
            )
            with self.assertRaises(RuntimeError):
                patch_app.patch_renderer_legacy(root, TEST_TOKEN_8378)

    def test_8378_anchors_are_not_applied_to_unknown_build(self) -> None:
        # The split anchor set is only reachable through an exact renderer
        # profile lookup; there is no build-name fallback.
        self.assertIsNone(
            patch_app.lookup_renderer_compatibility("26.903.61454", "8378", "arm64")
        )
        profiles = [
            (profile.version, profile.build, profile.architecture)
            for profile in patch_app.RENDERER_PROFILES
        ]
        self.assertIn(("26.903.61454", "8378", "x86_64"), profiles)
        self.assertIn(("26.803.61601", "6396", "arm64"), profiles)


if __name__ == "__main__":
    unittest.main()
