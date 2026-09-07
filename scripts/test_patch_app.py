"""Signing regression tests; no signing certificate or macOS tools required."""
import subprocess
import unittest
from unittest import mock

import patch_app


class SigningTeamTests(unittest.TestCase):
    def resolve(self, identity, metadata=("true", "TEAMID5678")):
        with mock.patch.object(patch_app.shutil, "copyfile") as copy, \
             mock.patch.object(patch_app, "run") as run, \
             mock.patch.object(patch_app, "signed_code_metadata", return_value=metadata):
            result = patch_app.signing_team_identifier(identity)
        return result, copy, run

    def test_development_name_suffix_is_not_the_team(self):
        result, copy, run = self.resolve("Apple Development: Example (PERSON1234)")
        self.assertEqual(result, "TEAMID5678")
        self.assertEqual(str(copy.call_args.args[0]), "/usr/bin/true")
        self.assertIn("Apple Development: Example (PERSON1234)", run.call_args.args[0])

    def test_certificate_fingerprint_selector_is_preserved(self):
        fingerprint = "A" * 40
        result, _, run = self.resolve(fingerprint)
        self.assertEqual(result, "TEAMID5678")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--sign") + 1], fingerprint)

    def test_developer_id_uses_actual_signature_team(self):
        result, _, _ = self.resolve("Developer ID Application: Example (TEAMID5678)")
        self.assertEqual(result, "TEAMID5678")

    def test_adhoc_does_not_invoke_signing_tools(self):
        with mock.patch.object(patch_app, "run") as run, \
             mock.patch.object(patch_app.shutil, "copyfile") as copy:
            self.assertIsNone(patch_app.signing_team_identifier("-"))
        run.assert_not_called()
        copy.assert_not_called()

    def test_missing_or_invalid_team_fails_closed(self):
        for team in (None, "not set", "short", "TEAMID5678\nother"):
            with self.subTest(team=team), self.assertRaises(RuntimeError):
                self.resolve("Apple Development: Example (PERSON1234)", ("true", team))

    def test_signing_failure_is_not_replaced_by_name_suffix(self):
        with mock.patch.object(patch_app.shutil, "copyfile"), \
             mock.patch.object(patch_app, "run", side_effect=subprocess.CalledProcessError(1, "codesign")), \
             self.assertRaises(subprocess.CalledProcessError):
            patch_app.signing_team_identifier("Apple Development: Example (PERSON1234)")


if __name__ == "__main__":
    unittest.main()
