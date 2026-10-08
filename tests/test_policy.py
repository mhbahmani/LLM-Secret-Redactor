import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from secret_redactor.broker import load_policy, restore_decision


class TestRestorePolicy(unittest.TestCase):
    def test_bundled_policy_defaults(self):
        policy = load_policy()
        self.assertEqual(restore_decision(policy, "Read"), "allow")
        self.assertEqual(restore_decision(policy, "WebFetch"), "deny")
        self.assertEqual(restore_decision(policy, "Bash"), "ask")
        self.assertEqual(restore_decision(policy, None), "ask")

    def test_custom_policy_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"default": "deny", "tools": {"bash": "allow"}}, handle)
        try:
            policy = load_policy(handle.name)
            self.assertEqual(restore_decision(policy, "bash"), "allow")
            self.assertEqual(restore_decision(policy, "Read"), "deny")
        finally:
            os.unlink(handle.name)

    def test_rejects_unknown_decisions(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"tools": {"bash": "sometimes"}}, handle)
        try:
            with self.assertRaises(ValueError):
                load_policy(handle.name)
        finally:
            os.unlink(handle.name)


if __name__ == "__main__":
    unittest.main()
