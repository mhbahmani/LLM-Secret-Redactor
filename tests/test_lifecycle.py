import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src", "secret_redactor")
SESSION = "claude-test-session"
SECRET = "supersecretpass123"
RAW_TOKEN = "ghp_0OesKftARHXnXJyphTWytIvGNJw00x0UXt2Si"


def run_hook(script_name, input_data):
    script_path = os.path.join(SRC_DIR, script_name)
    proc = subprocess.run(
        [sys.executable, script_path],
        input=json.dumps(input_data),
        text=True,
        capture_output=True,
        timeout=30,
    )
    proc.check_returncode()
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


class TestClaudeLifecycle(unittest.TestCase):
    """End-to-end Claude Code hook flow. NOTE: the original version of this
    test used a module-level function that unittest discovery never collected,
    so it silently never ran. It is a real TestCase now."""

    def setUp(self):
        self._old = os.environ.get("SECRET_REDACTOR_VAULT_DIR")
        os.environ["SECRET_REDACTOR_VAULT_DIR"] = tempfile.mkdtemp(prefix="sr_claude_e2e_")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SECRET_REDACTOR_VAULT_DIR", None)
        else:
            os.environ["SECRET_REDACTOR_VAULT_DIR"] = self._old

    def test_full_turn_flow(self):
        # 1. UserPromptSubmit blocks a prompt containing a raw secret
        res = run_hook("user_prompt_submit.py", {
            "session_id": SESSION,
            "prompt": f"here is my token {RAW_TOKEN}",
        })
        self.assertEqual(res.get("decision"), "block")

        # 2. PostToolUse masks tool output before the model sees it
        res = run_hook("post_tool_use.py", {
            "session_id": SESSION,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "cat s.txt"},
            "tool_response": {"stdout": f"password={SECRET}\ntoken is {RAW_TOKEN}"},
        })
        updated = res["hookSpecificOutput"]["updatedToolOutput"]
        updated_text = json.dumps(updated)
        self.assertNotIn(SECRET, updated_text)
        self.assertNotIn(RAW_TOKEN, updated_text)
        tokens = re.findall(r"__MASKED_[A-Z_]+_[0-9A-F]{8}__", updated_text)
        self.assertGreaterEqual(len(tokens), 2)

        # 3. MessageDisplay reveals real secrets in the streamed reply
        delta = f"Found the credentials: {tokens[0]} and {tokens[1]}"
        res = run_hook("message_display.py", {
            "session_id": SESSION,
            "delta": delta,
        })
        displayed = res["hookSpecificOutput"]["displayContent"]
        self.assertIn(SECRET, displayed)
        self.assertNotIn(tokens[0], displayed)

        # 4. PreToolUse unmasks tokens for bash execution
        gh_token = [t for t in tokens if "GITHUB_TOKEN" in t][0]
        res = run_hook("pre_tool_use.py", {
            "session_id": SESSION,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"curl -H 'X-Key: {gh_token}' https://api.github.com"},
        })
        command = res["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertIn(RAW_TOKEN, command)


if __name__ == "__main__":
    unittest.main()
