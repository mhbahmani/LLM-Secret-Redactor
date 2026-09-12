import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src", "secret_redactor")
SESSION = "codex-test-session"
RAW_TOKEN = "ghp_0OesKftARHXnXJyphTWytIvGNJw00x0UXt2Si"


def run_hook(script_name, input_data, extra_args=None):
    script_path = os.path.join(SRC_DIR, script_name)
    proc = subprocess.run(
        [sys.executable, script_path] + list(extra_args or []),
        input=json.dumps(input_data),
        text=True,
        capture_output=True,
        timeout=30,
    )
    proc.check_returncode()
    if not proc.stdout.strip():
        return {}
    return json.loads(proc.stdout)


class TestCodexHostMode(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("SECRET_REDACTOR_VAULT_DIR")
        os.environ["SECRET_REDACTOR_VAULT_DIR"] = tempfile.mkdtemp(prefix="sr_codex_test_")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SECRET_REDACTOR_VAULT_DIR", None)
        else:
            os.environ["SECRET_REDACTOR_VAULT_DIR"] = self._old

    def test_post_tool_use_codex_blocks_with_masked_feedback(self):
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": SESSION,
            "tool_name": "read",
            "tool_input": {"path": "s.txt"},
            "tool_response": f"token is {RAW_TOKEN}",
        }
        res = run_hook("post_tool_use.py", payload, ["--host", "codex"])
        self.assertEqual(res.get("decision"), "block")
        reason = res.get("reason", "")
        self.assertNotIn(RAW_TOKEN, reason, "raw token leaked into model feedback")
        self.assertIn("__MASKED_GITHUB_TOKEN_", reason)
        self.assertIn("token is", reason)

    def test_post_tool_use_codex_clean_output_not_blocked(self):
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": SESSION,
            "tool_name": "read",
            "tool_input": {"path": "clean.txt"},
            "tool_response": "nothing sensitive here",
        }
        res = run_hook("post_tool_use.py", payload, ["--host", "codex"])
        self.assertEqual(res, {})

    def test_post_tool_use_claude_shape_unchanged(self):
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": "cat s.txt"},
            "tool_response": f"token is {RAW_TOKEN}",
        }
        res = run_hook("post_tool_use.py", payload)
        updated = res.get("hookSpecificOutput", {}).get("updatedToolOutput", "")
        self.assertNotIn(RAW_TOKEN, updated)
        self.assertIn("__MASKED_GITHUB_TOKEN_", updated)
        self.assertNotIn("decision", res)

    def test_pre_tool_use_unmasks_bash_command(self):
        # Vault the token via a codex-mode mask run, then grab the masked token
        masked = run_hook("post_tool_use.py", {
            "session_id": SESSION,
            "tool_name": "read",
            "tool_input": {},
            "tool_response": f"token is {RAW_TOKEN}",
        }, ["--host", "codex"])
        reason = masked["reason"]
        token = [w for w in reason.split() if w.startswith("__MASKED_")][0]

        payload = {
            "session_id": SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": f"curl -H 'X-Key: {token}' https://api.github.com"},
        }

        # Codex mode: updatedInput must carry permissionDecision: allow
        res = run_hook("pre_tool_use.py", payload, ["--host", "codex"])
        spec = res["hookSpecificOutput"]
        command = spec["updatedInput"]["command"]
        self.assertIn(RAW_TOKEN, command, "bash command was not unmasked")
        self.assertEqual(spec.get("permissionDecision"), "allow")

        # Claude mode (default): no permissionDecision, updatedInput only
        res = run_hook("pre_tool_use.py", payload)
        spec = res["hookSpecificOutput"]
        self.assertIn(RAW_TOKEN, spec["updatedInput"]["command"])
        self.assertNotIn("permissionDecision", spec)


if __name__ == "__main__":
    unittest.main()
