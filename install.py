#!/usr/bin/env python3
import sys
import os
import re
import json
import shutil
import urllib.request
from datetime import datetime

APP_NAME = "secret-redactor"
REVEAL_COMMAND = "secret-redactor.ui.toggle"
DEFAULT_REVEAL_KEYBIND = "ctrl+shift+r"

CLAUDE_FILES = [
    ("broker.py", "broker.py"),
    ("redaction.py", "redaction.py"),
    ("vault.py", "vault.py"),
    ("patterns.json", "patterns.json"),
    ("policy.json", "policy.json"),
    ("session_start.py", "session_start.py"),
    ("session_end.py", "session_end.py"),
    ("user_prompt_submit.py", "user_prompt_submit.py"),
    ("post_tool_use.py", "post_tool_use.py"),
    ("message_display.py", "message_display.py"),
    ("pre_tool_use.py", "pre_tool_use.py")
]

OPENCODE_FILES = [
    ("broker.py", "broker.py"),
    ("redaction.py", "redaction.py"),
    ("vault.mjs", "vault.mjs"),
    ("patterns.json", "patterns.json"),
    ("policy.json", "policy.json"),
    ("opencode/transformer.mjs", "transformer.mjs"),
    ("opencode/hooks.mjs", "hooks.mjs"),
    ("opencode/plugin.mjs", "plugin.mjs"),
    ("opencode/tui.mjs", "tui.mjs"),
    ("opencode/ui-reveal.mjs", "ui-reveal.mjs"),
    ("opencode/package.json", "package.json")
]

REPO_RAW_URL = os.environ.get(
    "REPO_RAW_URL",
    "https://raw.githubusercontent.com/mhbahmani/llm-secret-redactor/master"
)

def prompt_menu(title, options):
    """
    Arrow key selector when tty available, fallback to numbered input.
    """
    tty_fd = None
    for candidate in ("/dev/tty", None):
        try:
            if candidate:
                fd = os.open(candidate, os.O_RDWR)
            else:
                fd = sys.stdin.fileno()
            if os.isatty(fd):
                import termios
                termios.tcgetattr(fd)
                tty_fd = fd
                break
        except Exception:
            continue

    if tty_fd is None:
        print(f"{title}:")
        for i, (name, desc) in enumerate(options):
            print(f"  {i + 1}) {name} - {desc}")
        try:
            choice = input(f"Choose option (1-{len(options)}) [1]: ").strip()
            idx = int(choice) - 1 if choice else 0
            if 0 <= idx < len(options):
                return idx
        except Exception:
            pass
        return 0

    import tty, termios
    old_settings = termios.tcgetattr(tty_fd)
    tty_out = os.fdopen(os.dup(tty_fd), 'w')
    selected = 0

    def render(first=False):
        if not first:
            tty_out.write(f"\033[{len(options)}A\r")
        for i, (name, desc) in enumerate(options):
            tty_out.write("\033[2K\r")
            if i == selected:
                tty_out.write(f"  \033[1;32m❯ {name:<12}\033[0m - {desc}\n")
            else:
                tty_out.write(f"    {name:<12} - {desc}\n")
        tty_out.flush()

    try:
        tty_out.write("\033[?25l")
        tty_out.write(f"{title} (use ↑/↓ arrow keys, Enter to confirm):\n")
        tty_out.flush()
        render(first=True)

        tty.setraw(tty_fd)
        while True:
            ch = os.read(tty_fd, 1)
            if ch in (b'\r', b'\n'):
                break
            elif ch == b'\x03':  # Ctrl+C
                raise KeyboardInterrupt
            elif ch == b'\x1b':
                seq = os.read(tty_fd, 2)
                if seq == b'[A':  # Up
                    selected = (selected - 1) % len(options)
                    render()
                elif seq == b'[B':  # Down
                    selected = (selected + 1) % len(options)
                    render()
            elif ch in (b'k', b'K'):
                selected = (selected - 1) % len(options)
                render()
            elif ch in (b'j', b'J'):
                selected = (selected + 1) % len(options)
                render()
    finally:
        try:
            termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_settings)
            tty_out.write("\033[?25h\n")
            tty_out.flush()
            tty_out.close()
            os.close(tty_fd)
        except Exception:
            pass

    return selected

def prompt_input(msg, default_val):
    try:
        if not sys.stdin.isatty():
            with open("/dev/tty", "r") as tty_in:
                sys.stderr.write(f"{msg} [{default_val}]: ")
                sys.stderr.flush()
                val = tty_in.readline().strip()
                return val if val else default_val
    except Exception:
        pass

    try:
        val = input(f"{msg} [{default_val}]: ").strip()
        return val if val else default_val
    except Exception:
        return default_val

def get_script_source(rel_path, local_repo_dir):
    # 1. Local repo check
    if local_repo_dir:
        candidate_paths = [
            os.path.join(local_repo_dir, "src", "secret_redactor", rel_path),
            os.path.join(local_repo_dir, rel_path)
        ]
        for p in candidate_paths:
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()

    # 2. Remote download
    urls = [
        f"{REPO_RAW_URL}/src/secret_redactor/{rel_path}",
        f"{REPO_RAW_URL}/{rel_path}"
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "llm-secret-redactor-installer"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return resp.read()
        except Exception:
            continue
    raise RuntimeError(f"Could not fetch {rel_path} from local files or {REPO_RAW_URL}")

def replace_install_dir(target_dir, files, local_repo_dir):
    """Fetch every file before touching target_dir, then replace it whole so
    files from older releases do not linger next to the new ones."""
    contents = [(dest_rel, get_script_source(src_rel, local_repo_dir)) for src_rel, dest_rel in files]
    if os.path.isdir(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir)
    for dest_rel, content in contents:
        target_path = os.path.join(target_dir, dest_rel)
        with open(target_path, "wb") as f:
            f.write(content)
        if target_path.endswith(".py"):
            os.chmod(target_path, 0o755)

# --- Claude Code Operations ---

def do_uninstall_claude(chosen_dir):
    print(f"\nUninstalling LLM Secret Redactor (Claude Code) from {chosen_dir}...")
    settings_file = os.path.join(chosen_dir, "settings.json")
    hooks_dir = os.path.join(chosen_dir, "hooks", APP_NAME)

    file_names = {dest for _, dest in CLAUDE_FILES}

    if os.path.isfile(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            settings = {}

        hooks = settings.get("hooks", {})
        modified = False

        for event, event_list in list(hooks.items()):
            new_event_list = []
            for group in event_list:
                remaining_hooks = []
                for h in group.get("hooks", []):
                    cmd = str(h.get("command", ""))
                    basename = os.path.basename(cmd)
                    is_redactor = (
                        f"/hooks/{APP_NAME}/" in cmd
                        or basename in file_names
                        or "claude_secret_vault" in cmd
                    )
                    if is_redactor:
                        modified = True
                    else:
                        remaining_hooks.append(h)
                if remaining_hooks:
                    group["hooks"] = remaining_hooks
                    new_event_list.append(group)
                else:
                    modified = True
            if new_event_list:
                hooks[event] = new_event_list
            else:
                del hooks[event]
                modified = True

        if modified:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"{settings_file}.backup_{ts}"
            shutil.copy2(settings_file, backup_file)
            print(f"Backed up settings before uninstall to {backup_file}")
            if not hooks and "hooks" in settings:
                del settings["hooks"]
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
            print(f"Cleaned redactor hooks from {settings_file}")
        else:
            print("No redactor hooks found in Claude Code settings.json")

    if os.path.isdir(hooks_dir):
        shutil.rmtree(hooks_dir)
        print(f"Removed Claude Code hooks directory: {hooks_dir}")

    parent_hooks = os.path.join(chosen_dir, "hooks")
    if os.path.isdir(parent_hooks) and not os.listdir(parent_hooks):
        os.rmdir(parent_hooks)

def do_install_claude(chosen_dir, local_repo_dir):
    hooks_dir = os.path.join(chosen_dir, "hooks", APP_NAME)
    settings_file = os.path.join(chosen_dir, "settings.json")

    print(f"\nInstalling Claude Code hooks into {hooks_dir}...")
    replace_install_dir(hooks_dir, CLAUDE_FILES, local_repo_dir)

    cwd = os.getcwd()
    if os.path.abspath(chosen_dir) == os.path.abspath(os.path.join(cwd, ".claude")):
        hook_path_prefix = f"${{CLAUDE_PROJECT_DIR}}/.claude/hooks/{APP_NAME}"
    else:
        hook_path_prefix = hooks_dir

    settings = {}
    if os.path.isfile(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            settings = {}

    hooks = settings.setdefault("hooks", {})
    hooks_def = {
        "SessionStart": f"{hook_path_prefix}/session_start.py",
        "SessionEnd": f"{hook_path_prefix}/session_end.py",
        "UserPromptSubmit": f"{hook_path_prefix}/user_prompt_submit.py",
        "PostToolUse": f"{hook_path_prefix}/post_tool_use.py",
        "MessageDisplay": f"{hook_path_prefix}/message_display.py",
        "PreToolUse": f"{hook_path_prefix}/pre_tool_use.py",
    }

    modified = False
    for event, cmd in hooks_def.items():
        event_list = hooks.setdefault(event, [])
        target_basename = os.path.basename(cmd)
        already_registered = False

        for group in event_list:
            for h in group.get("hooks", []):
                cur_cmd = h.get("command", "")
                if cur_cmd == cmd:
                    already_registered = True
                    break
                elif os.path.basename(str(cur_cmd)) == target_basename:
                    if cur_cmd != cmd:
                        h["command"] = cmd
                        modified = True
                    already_registered = True
                    break
            if already_registered:
                break

        if not already_registered:
            event_list.append({
                "hooks": [{"type": "command", "command": cmd}]
            })
            modified = True

    if modified or not os.path.exists(settings_file):
        if os.path.exists(settings_file):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"{settings_file}.backup_{ts}"
            shutil.copy2(settings_file, backup_file)
            print(f"Backed up Claude Code settings to {backup_file}")
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        print("Claude Code configuration updated.")
    else:
        print("Claude Code configuration is already up to date.")

    print(f"✓ Claude Code hooks installed in {hooks_dir}")

# --- OpenCode Operations ---

def find_opencode_config(base_dir, is_global):
    if is_global:
        for candidate in ["opencode.jsonc", "opencode.json"]:
            p = os.path.join(base_dir, candidate)
            if os.path.isfile(p):
                return p
        return os.path.join(base_dir, "opencode.jsonc")
    project_dir = os.path.dirname(base_dir) if os.path.basename(base_dir) == ".opencode" else base_dir
    for candidate in ["opencode.jsonc", "opencode.json"]:
        path = os.path.join(project_dir, candidate)
        if os.path.isfile(path):
            return path
    return os.path.join(project_dir, "opencode.json")

def remove_opencode_config(config_file, plugin_entries):
    if not os.path.isfile(config_file):
        return

    with open(config_file, "r", encoding="utf-8") as f:
        content = f.read()

    modified = False
    for entry in plugin_entries:
        quoted = rf'"{re.escape(entry)}"'
        new_content, count = re.subn(rf'{quoted}\s*,', '', content, count=1)
        if not count:
            new_content, count = re.subn(rf',\s*{quoted}', '', content, count=1)
        if not count:
            new_content, count = re.subn(quoted, '', content, count=1)
        if count:
            content = new_content
            modified = True

    if modified:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"{config_file}.backup_{ts}"
        shutil.copy2(config_file, backup_file)
        print(f"Backed up OpenCode config to {backup_file}")
        with open(config_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Removed redactor plugin entry from {config_file}")

def opencode_tui_config_path(chosen_dir):
    override = os.environ.get("OPENCODE_TUI_CONFIG")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(chosen_dir, "tui.json")

def update_opencode_tui_config(chosen_dir, keybind):
    config_file = opencode_tui_config_path(chosen_dir)
    config = {}
    if os.path.isfile(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
    plugin_entry = f"./plugins/{APP_NAME}/tui.mjs"
    plugins = config.setdefault("plugin", [])
    keybinds = config.setdefault("keybinds", {})
    changed = False
    if plugin_entry not in plugins:
        plugins.append(plugin_entry)
        changed = True
    if keybinds.get(REVEAL_COMMAND) != keybind:
        keybinds[REVEAL_COMMAND] = keybind
        changed = True
    if not changed:
        return
    if os.path.isfile(config_file):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(config_file, f"{config_file}.backup_{ts}")
    else:
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        config.setdefault("$schema", "https://opencode.ai/tui.json")
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print(f"Configured OpenCode reveal UI and keybinding {keybind!r} in {config_file}")

def remove_opencode_tui_config(chosen_dir):
    config_file = opencode_tui_config_path(chosen_dir)
    if not os.path.isfile(config_file):
        return
    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)
    keybinds = config.get("keybinds", {})
    plugins = config.get("plugin", [])
    plugin_entry = f"./plugins/{APP_NAME}/tui.mjs"
    changed = False
    if REVEAL_COMMAND in keybinds:
        del keybinds[REVEAL_COMMAND]
        changed = True
    if plugin_entry in plugins:
        config["plugin"] = [entry for entry in plugins if entry != plugin_entry]
        changed = True
    if not changed:
        return
    if not keybinds:
        config.pop("keybinds", None)
    if not config.get("plugin"):
        config.pop("plugin", None)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(config_file, f"{config_file}.backup_{ts}")
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print(f"Removed OpenCode reveal UI configuration from {config_file}")

def do_uninstall_opencode(chosen_dir, is_global):
    print(f"\nUninstalling LLM Secret Redactor (OpenCode) from {chosen_dir}...")
    plugins_dir = os.path.join(chosen_dir, "plugins", APP_NAME)
    trampoline = os.path.join(chosen_dir, "plugins", f"{APP_NAME}.js")
    config_file = find_opencode_config(chosen_dir, is_global)

    remove_entries = [
        f"./plugins/{APP_NAME}/plugin.js",
        f"./.opencode/plugins/{APP_NAME}/plugin.js",
        f"./plugins/{APP_NAME}.js",
        f"./.opencode/plugins/{APP_NAME}.js",
    ]
    remove_opencode_config(config_file, remove_entries)
    remove_opencode_tui_config(chosen_dir)

    if os.path.isdir(plugins_dir):
        shutil.rmtree(plugins_dir)
        print(f"Removed OpenCode plugin directory: {plugins_dir}")

    if os.path.isfile(trampoline):
        os.remove(trampoline)
        print(f"Removed OpenCode plugin trampoline: {trampoline}")

    redactor_config = os.path.join(chosen_dir, "secret-redactor.json")
    if os.path.isfile(redactor_config):
        os.remove(redactor_config)
        print(f"Removed {redactor_config}")

    parent_plugins = os.path.join(chosen_dir, "plugins")
    if os.path.isdir(parent_plugins) and not os.listdir(parent_plugins):
        os.rmdir(parent_plugins)

def do_install_opencode(chosen_dir, is_global, local_repo_dir, reveal_keybind=DEFAULT_REVEAL_KEYBIND):
    plugins_dir = os.path.join(chosen_dir, "plugins", APP_NAME)

    print(f"\nInstalling OpenCode plugin into {plugins_dir}...")
    replace_install_dir(plugins_dir, OPENCODE_FILES, local_repo_dir)

    trampoline = os.path.join(chosen_dir, "plugins", f"{APP_NAME}.js")
    with open(trampoline, "w", encoding="utf-8") as f:
        f.write(f'export {{ default }} from "./{APP_NAME}/plugin.mjs";\n')

    # OpenCode auto-loads files in its global and project plugin directories.
    # Registering the nested implementation in opencode.json as well would load
    # the redactor twice, so the top-level trampoline is the only entry point.

    update_opencode_tui_config(chosen_dir, reveal_keybind)

    print(f"✓ OpenCode plugin installed in {plugins_dir}")

# --- Main Entry ---

def parse_args():
    action = None
    if "--uninstall" in sys.argv or "uninstall" in sys.argv:
        action = "uninstall"
    elif "--install" in sys.argv or "install" in sys.argv:
        action = "install"

    scope = None
    if "--global" in sys.argv:
        scope = "global"
    elif "--local" in sys.argv:
        scope = "local"
    for arg in sys.argv:
        if arg.startswith("--scope="):
            scope = arg.split("=", 1)[1].lower()

    client = None
    if "--all" in sys.argv:
        client = "all"
    elif "--claude" in sys.argv:
        client = "claude"
    elif "--opencode" in sys.argv:
        client = "opencode"
    for arg in sys.argv:
        if arg.startswith("--client="):
            client = arg.split("=", 1)[1].lower()

    reveal_keybind = None
    for arg in sys.argv:
        if arg.startswith("--reveal-keybind="):
            reveal_keybind = arg.split("=", 1)[1]

    return action, scope, client, reveal_keybind

def main():
    print("==========================================")
    print("      LLM Secret Redactor Installer       ")
    print("    Supports Claude Code and OpenCode     ")
    print("==========================================")
    print("")

    caller_dir = None
    if "__file__" in globals() and os.path.isfile(__file__):
        caller_dir = os.path.dirname(os.path.abspath(__file__))

    cli_action, cli_scope, cli_client, cli_reveal_keybind = parse_args()

    # 1. Action
    if cli_action:
        action = cli_action
    else:
        action_options = [
            ("Install", "Install or update LLM secret redactor"),
            ("Uninstall", "Cleanly remove redactor integrations and files")
        ]
        action_idx = prompt_menu("Select action", action_options)
        action = "uninstall" if action_idx == 1 else "install"

    # 2. Client Selection
    if cli_client:
        client = cli_client
    else:
        client_options = [
            ("Both", "Install/Update for both Claude Code and OpenCode (Recommended)"),
            ("Claude Code", "Target Claude Code native hooks only"),
            ("OpenCode", "Target OpenCode plugin only")
        ]
        client_idx = prompt_menu("Select target client", client_options)
        if client_idx == 0:
            client = "all"
        elif client_idx == 1:
            client = "claude"
        else:
            client = "opencode"

    # 3. Scope Selection
    if cli_scope:
        scope = cli_scope
    else:
        scope_options = [
            ("Local", "Current project: applies only to this directory/repository"),
            ("Global", "User-wide: applies across all sessions on your system")
        ]
        scope_idx = prompt_menu("Select scope", scope_options)
        scope = "global" if scope_idx == 1 else "local"

    is_global = (scope == "global")

    reveal_keybind = cli_reveal_keybind or DEFAULT_REVEAL_KEYBIND
    if client in ("opencode", "all"):
        if action == "install" and cli_reveal_keybind is None:
            reveal_keybind = prompt_input("OpenCode reveal keybinding", DEFAULT_REVEAL_KEYBIND)

    print(f"\nSelected action: {action.upper()}")
    print(f"Selected client: {client.upper()}")
    print(f"Selected scope:  {scope.capitalize()}")
    if client in ("opencode", "all"):
        print(f"Reveal keybind:   {reveal_keybind}")

    # Paths
    if client in ("claude", "all"):
        default_claude_dir = os.path.expanduser("~/.claude") if is_global else os.path.abspath(".claude")
        chosen_claude_dir = prompt_input(
            "Claude Code destination directory (press Enter for default)",
            default_claude_dir
        )
        chosen_claude_dir = os.path.abspath(os.path.expanduser(chosen_claude_dir))

        if action == "uninstall":
            do_uninstall_claude(chosen_claude_dir)
        else:
            do_install_claude(chosen_claude_dir, caller_dir)

    if client in ("opencode", "all"):
        default_opencode_dir = os.path.expanduser("~/.config/opencode") if is_global else os.path.abspath(".opencode")
        chosen_opencode_dir = prompt_input(
            "OpenCode destination directory (press Enter for default)",
            default_opencode_dir
        )
        chosen_opencode_dir = os.path.abspath(os.path.expanduser(chosen_opencode_dir))

        if action == "uninstall":
            do_uninstall_opencode(chosen_opencode_dir, is_global)
        else:
            do_install_opencode(chosen_opencode_dir, is_global, caller_dir, reveal_keybind)

    print("\n==========================================")
    print(f"✓ {action.capitalize()} completed successfully!")
    print("==========================================")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
