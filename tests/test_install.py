import json
import os
import tempfile
import unittest

import install


class OpenCodeInstallerTests(unittest.TestCase):
    def test_local_install_uses_plugin_autoload_without_mutating_project_config(self):
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as project_dir:
            config_path = os.path.join(project_dir, "opencode.json")
            original_config = {"model": "example/model"}
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump(original_config, handle)

            destination = os.path.join(project_dir, ".opencode")
            install.do_install_opencode(destination, False, repo_dir)

            with open(config_path, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), original_config)
            self.assertTrue(os.path.isfile(os.path.join(destination, "plugins", "secret-redactor.js")))
            self.assertTrue(os.path.isfile(os.path.join(destination, "plugins", "secret-redactor", "plugin.mjs")))
            self.assertTrue(os.path.isfile(os.path.join(destination, "plugins", "secret-redactor", "broker.py")))
            self.assertTrue(os.path.isfile(os.path.join(destination, "plugins", "secret-redactor", "tui.mjs")))
            with open(os.path.join(destination, "tui.json"), "r", encoding="utf-8") as handle:
                tui_config = json.load(handle)
            self.assertEqual(
                tui_config["keybinds"][install.REVEAL_COMMAND],
                install.DEFAULT_REVEAL_KEYBIND,
            )
            self.assertIn("./plugins/secret-redactor/tui.mjs", tui_config["plugin"])

    def test_uninstall_removes_legacy_registration_without_breaking_json(self):
        with tempfile.TemporaryDirectory() as project_dir:
            destination = os.path.join(project_dir, ".opencode")
            os.makedirs(destination)
            config_path = os.path.join(project_dir, "opencode.json")
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "plugin": ["./other.js", "./.opencode/plugins/secret-redactor/plugin.js"],
                    "model": "example/model",
                }, handle)

            install.do_uninstall_opencode(destination, False)

            with open(config_path, "r", encoding="utf-8") as handle:
                config = json.load(handle)
            self.assertEqual(config["plugin"], ["./other.js"])
            self.assertEqual(config["model"], "example/model")

    def test_uninstall_removes_only_redactor_tui_settings(self):
        with tempfile.TemporaryDirectory() as project_dir:
            destination = os.path.join(project_dir, ".opencode")
            os.makedirs(destination)
            tui_path = os.path.join(destination, "tui.json")
            with open(tui_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "plugin": ["./plugins/other.mjs", "./plugins/secret-redactor/tui.mjs"],
                    "keybinds": {
                        "other.command": "ctrl+o",
                        install.REVEAL_COMMAND: "ctrl+shift+r",
                    },
                }, handle)

            install.do_uninstall_opencode(destination, False)

            with open(tui_path, "r", encoding="utf-8") as handle:
                config = json.load(handle)
            self.assertEqual(config["plugin"], ["./plugins/other.mjs"])
            self.assertEqual(config["keybinds"], {"other.command": "ctrl+o"})

    def test_claude_install_registers_broker_lifecycle(self):
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as claude_dir:
            install.do_install_claude(claude_dir, repo_dir)
            with open(os.path.join(claude_dir, "settings.json"), "r", encoding="utf-8") as handle:
                settings = json.load(handle)
            self.assertIn("SessionStart", settings["hooks"])
            self.assertIn("SessionEnd", settings["hooks"])
            self.assertTrue(os.path.isfile(os.path.join(
                claude_dir, "hooks", "secret-redactor", "broker.py"
            )))

    def test_reinstall_removes_files_from_older_releases(self):
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as project_dir:
            destination = os.path.join(project_dir, ".opencode")
            stale = os.path.join(destination, "plugins", "secret-redactor", "v2.js")
            os.makedirs(os.path.dirname(stale))
            with open(stale, "w", encoding="utf-8") as handle:
                handle.write("// old release")

            install.do_install_opencode(destination, False, repo_dir)

            self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.isfile(os.path.join(
                destination, "plugins", "secret-redactor", "plugin.mjs"
            )))

    def test_parse_args_accepts_flags_or_command(self):
        self.assertEqual(
            install.parse_args(["--install", "--opencode", "--local", "--reveal-keybind=ctrl+k"]),
            ("install", "local", "opencode", "ctrl+k", None),
        )
        self.assertEqual(
            install.parse_args(["uninstall", "--client=CLAUDE", "--scope", "global", "--ref", "v1.2.0"]),
            ("uninstall", "global", "claude", None, "v1.2.0"),
        )

    def test_claude_install_adds_redact_command(self):
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as claude_dir:
            install.do_install_claude(claude_dir, repo_dir)
            with open(os.path.join(claude_dir, "settings.json"), "r", encoding="utf-8") as handle:
                groups = json.load(handle)["hooks"]["UserPromptExpansion"]
            self.assertEqual(groups[0]["matcher"], "redact")
            self.assertTrue(groups[0]["hooks"][0]["command"].endswith("/redact_command.py"))
            command = os.path.join(claude_dir, "commands", "redact.md")
            with open(command, "r", encoding="utf-8") as handle:
                self.assertIn("disable-model-invocation: true", handle.read())

            install.do_uninstall_claude(claude_dir)
            self.assertFalse(os.path.exists(command))
            with open(os.path.join(claude_dir, "settings.json"), "r", encoding="utf-8") as handle:
                self.assertNotIn("UserPromptExpansion", json.load(handle).get("hooks", {}))

    def test_claude_install_keeps_a_users_own_redact_command(self):
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as claude_dir:
            command = os.path.join(claude_dir, "commands", "redact.md")
            os.makedirs(os.path.dirname(command))
            with open(command, "w", encoding="utf-8") as handle:
                handle.write("my own command")
            install.do_install_claude(claude_dir, repo_dir)
            install.do_uninstall_claude(claude_dir)
            with open(command, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "my own command")


if __name__ == "__main__":
    unittest.main()
