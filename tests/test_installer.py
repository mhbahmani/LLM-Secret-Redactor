import importlib.util
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_installer():
    spec = importlib.util.spec_from_file_location(
        "installer", os.path.join(REPO_ROOT, "install.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRegisterHooks(unittest.TestCase):
    def setUp(self):
        self.installer = load_installer()

    def test_registers_matcher_spec(self):
        hooks = {}
        modified = self.installer.register_hooks(hooks, {
            "PreToolUse": {"command": "/x/pre_tool_use.py", "matcher": "^Bash$"},
            "PostToolUse": "/x/post_tool_use.py --host codex",
        })
        self.assertTrue(modified)
        self.assertEqual(hooks["PreToolUse"][0].get("matcher"), "^Bash$")
        self.assertEqual(
            hooks["PostToolUse"][0]["hooks"][0]["command"],
            "/x/post_tool_use.py --host codex",
        )

    def test_idempotent(self):
        hooks = {}
        spec = {"PreToolUse": {"command": "/x/pre_tool_use.py", "matcher": "^Bash$"}}
        self.installer.register_hooks(hooks, spec)
        modified = self.installer.register_hooks(hooks, spec)
        self.assertFalse(modified)
        self.assertEqual(len(hooks["PreToolUse"]), 1)

    def test_upgrades_legacy_entry_without_matcher(self):
        # Simulate an older install: command present but no matcher
        hooks = {
            "PreToolUse": [
                {"hooks": [{"type": "command", "command": "/old/pre_tool_use.py"}]}
            ]
        }
        modified = self.installer.register_hooks(hooks, {
            "PreToolUse": {"command": "/x/pre_tool_use.py --host codex", "matcher": "^Bash$"}
        })
        self.assertTrue(modified)
        group = hooks["PreToolUse"][0]
        self.assertEqual(group.get("matcher"), "^Bash$")
        self.assertEqual(
            group["hooks"][0]["command"], "/x/pre_tool_use.py --host codex"
        )
        self.assertEqual(len(hooks["PreToolUse"]), 1)

    def test_uninstall_detects_commands_with_args(self):
        hooks = {
            "PostToolUse": [
                {"hooks": [{"type": "command", "command": 'python3 "/x/post_tool_use.py" --host codex'}]}
            ]
        }
        modified = self.installer.strip_redactor_hooks(hooks)
        self.assertTrue(modified)
        self.assertNotIn("PostToolUse", hooks)


if __name__ == "__main__":
    unittest.main()
