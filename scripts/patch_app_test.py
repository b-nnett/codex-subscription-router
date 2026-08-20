from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import patch_app


class PatchAppSigningTests(unittest.TestCase):
    def test_signs_real_codex_child_with_router_team(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "Codex Subscription Router.app"
            real_codex = app / "Contents" / "Resources" / "codex.real"
            real_codex.parent.mkdir(parents=True)
            real_codex.write_bytes(b"test executable")

            with (
                mock.patch.object(
                    patch_app,
                    "capture_computer_use_entitlements",
                    return_value={},
                ),
                mock.patch.object(patch_app, "patch_computer_use_identity"),
                mock.patch.object(patch_app, "sign_computer_use_code"),
                mock.patch.object(patch_app, "sign_runtime_executable") as sign_runtime,
                mock.patch.object(patch_app, "run"),
            ):
                patch_app.sign_independent_app(
                    app,
                    "Apple Development: Test",
                    "TESTTEAM01",
                    99,
                )

            sign_runtime.assert_any_call(
                real_codex,
                "Apple Development: Test",
                "codex",
            )


if __name__ == "__main__":
    unittest.main()
