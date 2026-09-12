import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO_ROOT, "src", "secret_redactor", "cli.py")
SECRET = "AKIAIOSFODNN7EXAMPLE"


def run_cli(mode, payload=None, raw_stdin=None, extra_args=None, as_text=False):
    stdin = raw_stdin if raw_stdin is not None else json.dumps(payload or {})
    result = subprocess.run(
        [sys.executable, CLI, mode] + list(extra_args or []),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout if as_text else json.loads(result.stdout)


class TestCli(unittest.TestCase):
    def setUp(self):
        self._old_vault_dir = os.environ.get("SECRET_REDACTOR_VAULT_DIR")
        os.environ["SECRET_REDACTOR_VAULT_DIR"] = tempfile.mkdtemp(prefix="sr_cli_test_")

    def tearDown(self):
        if self._old_vault_dir is None:
            os.environ.pop("SECRET_REDACTOR_VAULT_DIR", None)
        else:
            os.environ["SECRET_REDACTOR_VAULT_DIR"] = self._old_vault_dir

    def test_mask_unmask_round_trip(self):
        masked = run_cli("mask", {"session_id": "t1", "data": f"key = '{SECRET}'"})
        self.assertTrue(masked["changed"])
        self.assertNotIn(SECRET, masked["data"])
        self.assertRegex(masked["data"], r"__MASKED_AWS_KEY_[0-9A-F]{8}__")

        restored = run_cli("unmask", {"session_id": "t1", "data": masked["data"]})
        self.assertTrue(restored["changed"])
        self.assertIn(SECRET, restored["data"])

    def test_mask_recursive_nested_structures(self):
        payload = {"env": [f"TOKEN={SECRET}"], "note": "clean"}
        masked = run_cli("mask", {"session_id": "t2", "data": payload})
        self.assertTrue(masked["changed"])
        self.assertNotIn(SECRET, json.dumps(masked["data"]))

    def test_check_detects_and_ignores_masked(self):
        kind = run_cli("check", {"data": f"please use {SECRET}"})
        self.assertEqual(kind["secret_type"], "AWS_KEY")
        clean = run_cli("check", {"data": "nothing to see here"})
        self.assertEqual(clean["secret_type"], "")
        already_masked = run_cli("check", {"data": "token is __MASKED_AWS_KEY_AABBCCDD__"})
        self.assertEqual(already_masked["secret_type"], "")

    def test_invalid_stdin_fails_open(self):
        result = subprocess.run(
            [sys.executable, CLI, "mask"],
            input="not json at all",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout)
        self.assertFalse(out["changed"])

    def test_reveal_restores_masked_text_for_humans(self):
        masked = run_cli("mask", {"session_id": "t9", "data": f"token is {SECRET}"})
        token = [w for w in masked["data"].split() if w.startswith("__MASKED_")][0]

        # Reveal without --session: searches all vaults
        revealed = run_cli("reveal", raw_stdin=f"printed: token is {token}\n", as_text=True)
        self.assertIn(SECRET, revealed)
        self.assertNotIn(token, revealed)

        # Reveal with --session restricted to an unrelated session: no change
        revealed = run_cli("reveal", raw_stdin=token, extra_args=["--session", "other"], as_text=True)
        self.assertIn(token, revealed)


if __name__ == "__main__":
    unittest.main()
