"""End-to-end Codex turn scenario, derived from real usage:

    1. prompt with a raw secret must be blocked
    2. tool output must be masked: the model receives ONLY redacted text
    3. file writes keep the masked token (no unmask outside Bash)
    4. bash commands get tokens unmasked (with permissionDecision: allow)
    5. when the assistant prints the result, the Stop hook must reveal the
       secret to the human via systemMessage

Runs against the INSTALLED artifact (files + hooks.json produced by
do_install_codex), not the repo sources, so registration mistakes
(matcher, --host flags, missing scripts) fail here too.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import importlib.util

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION = "codex-e2e-session"
RAW_TOKEN = "ghp_0OesKftARHXnXJyphTWytIvGNJw00x0UXt2Si"


def load_installer():
    spec = importlib.util.spec_from_file_location(
        "installer", os.path.join(REPO_ROOT, "install.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCodexTurnLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = load_installer()
        cls.tmp = tempfile.mkdtemp(prefix="sr_codex_turn_")
        os.environ["SECRET_REDACTOR_VAULT_DIR"] = os.path.join(cls.tmp, "vault")
        cls.installer.do_install_codex(cls.tmp, REPO_ROOT)
        cls.hooks_dir = os.path.join(cls.tmp, "hooks", "secret-redactor")
        with open(os.path.join(cls.tmp, "hooks.json"), encoding="utf-8") as f:
            cls.hooks_config = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.pop("SECRET_REDACTOR_VAULT_DIR", None)

    def run_hook(self, name, payload):
        script = os.path.join(self.hooks_dir, name)
        args = [sys.executable, script]
        if name in ("pre_tool_use.py", "post_tool_use.py"):
            args += ["--host", "codex"]
        proc = subprocess.run(
            args, input=json.dumps(payload), capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip() else {}

    def test_registration_matches_codex_contract(self):
        cfg = self.hooks_config["hooks"]
        self.assertIn("UserPromptSubmit", cfg)
        self.assertIn("Stop", cfg)
        self.assertEqual(cfg["PostToolUse"][0]["hooks"][0]["command"].endswith("--host codex"), True)
        for group in cfg["PreToolUse"]:
            self.assertEqual(group.get("matcher"), "^Bash$")
            self.assertTrue(group["hooks"][0]["command"].endswith("--host codex"))

    def test_full_turn_scenario(self):
        # 1. Prompt containing the raw secret is blocked; clean prompt passes
        res = self.run_hook("user_prompt_submit.py", {
            "session_id": SESSION, "prompt": f"please use {RAW_TOKEN}",
        })
        self.assertEqual(res.get("decision"), "block")
        res = self.run_hook("user_prompt_submit.py", {
            "session_id": SESSION, "prompt": "read s.txt",
        })
        self.assertEqual(res, {})

        # 2. Tool output: model receives ONLY the masked feedback
        res = self.run_hook("post_tool_use.py", {
            "session_id": SESSION,
            "tool_name": "read",
            "tool_input": {"path": "s.txt"},
            "tool_response": f"token is {RAW_TOKEN}",
        })
        self.assertEqual(res.get("decision"), "block")
        reason = res.get("reason", "")
        self.assertNotIn(RAW_TOKEN, reason, "raw token reached the model")
        token = next(w for w in reason.split() if w.startswith("__MASKED_"))

        # 3. File writes keep the masked token: PreToolUse only applies to Bash
        for group in self.hooks_config["hooks"]["PreToolUse"]:
            self.assertEqual(group.get("matcher"), "^Bash$")

        # 4. Bash command with the masked token is unmasked + allowed
        res = self.run_hook("pre_tool_use.py", {
            "session_id": SESSION,
            "tool_name": "Bash",
            "tool_input": {"command": f"curl -H 'X-Key: {token}' https://api.github.com"},
        })
        spec = res["hookSpecificOutput"]
        self.assertIn(RAW_TOKEN, spec["updatedInput"]["command"])
        self.assertEqual(spec["permissionDecision"], "allow")

        # 5. Assistant prints the masked token -> Stop hook REVEALS it to the human,
        #    showing only the lines that contained secrets (no boilerplate echo)
        res = self.run_hook("stop_reveal.py", {
            "session_id": SESSION,
            "last_assistant_message": (
                "s.txt contains: token is {t}\n\n"
                "The secret was automatically masked before reaching me."
            ).format(t=token),
        })
        revealed = res.get("systemMessage", "")
        self.assertIn(RAW_TOKEN, revealed, "human was not shown the real secret")
        self.assertNotIn(token, revealed)
        self.assertNotIn("automatically masked", revealed, "reveal must not echo boilerplate")
        self.assertNotIn("```", revealed, "reveal must not echo code fences")

        # 6. Reply without tokens produces no systemMessage noise
        res = self.run_hook("stop_reveal.py", {
            "session_id": SESSION,
            "last_assistant_message": "all done, nothing sensitive",
        })
        self.assertEqual(res, {})


if __name__ == "__main__":
    unittest.main()
