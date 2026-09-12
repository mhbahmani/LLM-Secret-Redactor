#!/usr/bin/env python3
import sys
import os
import json
import shutil
import urllib.request
from datetime import datetime

APP_NAME = "secret-redactor"
# Python hook wrappers (Claude Code + Codex adapters)
FILES = [
    "vault.py",
    "user_prompt_submit.py",
    "post_tool_use.py",
    "message_display.py",
    "pre_tool_use.py"
]
# Core-only files for opencode (its TS adapter talks to cli.py)
OPENCODE_CORE_FILES = [
    "vault.py",
    "cli.py"
]
# Codex reuses the Python wrappers but has no display hook, so MessageDisplay
# is not part of its surface. PostToolUse runs in codex mode (block + masked
# feedback), PreToolUse is matched to Bash only, and stop_reveal.py surfaces
# the assistant's reply with tokens restored as a systemMessage (the only
# reveal path Codex offers). cli.py ships so users can reveal masked output
# locally too.
CODEX_FILES = [
    "vault.py",
    "cli.py",
    "user_prompt_submit.py",
    "post_tool_use.py",
    "pre_tool_use.py",
    "stop_reveal.py"
]
OPENCODE_PLUGIN_FILE = "secret-redactor.ts"
REPO_RAW_URL = os.environ.get(
    "REPO_RAW_URL",
    "https://raw.githubusercontent.com/mhbahmani/llm-secret-redactor/master"
)


def prompt_menu(title, options):
    """
    Arrow key selector when tty available, fallback to numbered input.
    """
    # Check if we can interact with a real terminal
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
        # Non-interactive fallback
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

    # Interactive TTY with arrow keys
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
                tty_out.write(f"  \033[1;32m❯ {name:<10}\033[0m - {desc}\n")
            else:
                tty_out.write(f"    {name:<10} - {desc}\n")
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
        # Try reading from /dev/tty if stdin is piped
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


def get_script_source(filename, local_repo_dir):
    # 1. Local repo check
    if local_repo_dir:
        candidate_paths = [
            os.path.join(local_repo_dir, "src", "secret_redactor", filename),
            os.path.join(local_repo_dir, "opencode", filename),
            os.path.join(local_repo_dir, filename)
        ]
        for p in candidate_paths:
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()

    # 2. Remote download
    urls = [
        f"{REPO_RAW_URL}/src/secret_redactor/{filename}",
        f"{REPO_RAW_URL}/opencode/{filename}",
        f"{REPO_RAW_URL}/{filename}"
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "llm-secret-redactor-installer"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return resp.read()
        except Exception:
            continue
    raise RuntimeError(f"Could not fetch {filename} from local files or {REPO_RAW_URL}")


def install_files(filenames, target_dir, local_repo_dir):
    os.makedirs(target_dir, exist_ok=True)
    print(f"\nInstalling hook scripts into {target_dir}...")
    for filename in filenames:
        content = get_script_source(filename, local_repo_dir)
        target_path = os.path.join(target_dir, filename)
        with open(target_path, "wb") as f:
            f.write(content)
        os.chmod(target_path, 0o755)


def load_json_file(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def backup_file(path):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{path}.backup_{ts}"
    shutil.copy2(path, backup_path)
    print(f"Backed up existing settings to {backup_path}")


def hook_script_name(cmd):
    """Extract the script basename from a hook command (which may carry args)."""
    for token in cmd.replace('"', " ").replace("'", " ").split():
        if token.endswith(".py") or token.endswith(".ts"):
            return os.path.basename(token)
    return os.path.basename(cmd)


def is_redactor_command(cmd):
    return (
        f"/hooks/{APP_NAME}/" in cmd
        or hook_script_name(cmd) in set(FILES) | {OPENCODE_PLUGIN_FILE}
        or "claude_secret_vault" in cmd
    )


def strip_redactor_hooks(hooks):
    """Remove redactor entries from a {Event: [{hooks: [...]}]} mapping in place."""
    modified = False
    for event, event_list in list(hooks.items()):
        new_event_list = []
        for group in event_list:
            remaining_hooks = [h for h in group.get("hooks", []) if not is_redactor_command(str(h.get("command", "")))]
            if len(remaining_hooks) != len(group.get("hooks", [])):
                modified = True
            if remaining_hooks:
                group["hooks"] = remaining_hooks
                new_event_list.append(group)
            else:
                modified = True
        if new_event_list:
            hooks[event] = new_event_list
        else:
            hooks.pop(event, None)
            modified = True
    return modified


def register_hooks(hooks, hooks_def):
    """Idempotently register {Event: command-or-spec} entries into a hooks mapping.

    A spec value may be a plain command string or {"command": ..., "matcher": ...}.
    """
    modified = False
    for event, spec in hooks_def.items():
        if isinstance(spec, str):
            cmd, matcher = spec, None
        else:
            cmd, matcher = spec["command"], spec.get("matcher")
        event_list = hooks.setdefault(event, [])
        target_basename = hook_script_name(cmd)
        already_registered = False

        for group in event_list:
            for h in group.get("hooks", []):
                cur_cmd = h.get("command", "")
                if cur_cmd == cmd:
                    already_registered = True
                    break
                elif hook_script_name(str(cur_cmd)) == target_basename:
                    if cur_cmd != cmd:
                        h["command"] = cmd
                        modified = True
                    # Upgrade older installs that lack the desired matcher
                    if matcher and group.get("matcher") != matcher:
                        group["matcher"] = matcher
                        modified = True
                    already_registered = True
                    break
            if already_registered:
                break

        if already_registered:
            continue

        entry = {"hooks": [{"type": "command", "command": cmd}]}
        if matcher:
            entry["matcher"] = matcher
        event_list.append(entry)
        modified = True
    return modified


def clean_hook_config(config_file):
    """Strip redactor entries from a config file with a top-level 'hooks' key."""
    settings = load_json_file(config_file)
    if settings is None:
        print(f"No config found at {config_file}")
        return
    hooks = settings.get("hooks", {})
    if strip_redactor_hooks(hooks):
        backup_file(config_file)
        if not hooks:
            settings.pop("hooks", None)
        save_json_file(config_file, settings)
        print(f"Cleaned redactor hooks from {config_file}")
    else:
        print(f"No redactor hooks found in {config_file}")


def remove_dir_if_empty(path):
    if os.path.isdir(path) and not os.listdir(path):
        os.rmdir(path)


def uninstall_banner(tool, chosen_dir):
    print(f"\nUninstalling LLM Secret Redactor ({tool}) from {chosen_dir}...")
    print("")


def uninstall_footer():
    print("==========================================")
    print("✓ Uninstallation complete!")
    print("==========================================")


def do_uninstall_claude(chosen_dir):
    uninstall_banner("Claude Code", chosen_dir)
    clean_hook_config(os.path.join(chosen_dir, "settings.json"))

    hooks_dir = os.path.join(chosen_dir, "hooks", APP_NAME)
    if os.path.isdir(hooks_dir):
        shutil.rmtree(hooks_dir)
        print(f"Removed dedicated directory: {hooks_dir}")
    remove_dir_if_empty(os.path.join(chosen_dir, "hooks"))
    uninstall_footer()


def do_uninstall_codex(chosen_dir):
    uninstall_banner("Codex", chosen_dir)
    clean_hook_config(os.path.join(chosen_dir, "hooks.json"))

    hooks_dir = os.path.join(chosen_dir, "hooks", APP_NAME)
    if os.path.isdir(hooks_dir):
        shutil.rmtree(hooks_dir)
        print(f"Removed dedicated directory: {hooks_dir}")
    remove_dir_if_empty(os.path.join(chosen_dir, "hooks"))
    uninstall_footer()


def do_uninstall_opencode(chosen_dir):
    uninstall_banner("opencode", chosen_dir)

    core_dir = os.path.join(chosen_dir, "secret-redactor")
    if os.path.isdir(core_dir):
        shutil.rmtree(core_dir)
        print(f"Removed core directory: {core_dir}")

    plugin_file = os.path.join(chosen_dir, "plugins", OPENCODE_PLUGIN_FILE)
    if os.path.isfile(plugin_file):
        os.remove(plugin_file)
        print(f"Removed plugin: {plugin_file}")

    remove_dir_if_empty(os.path.join(chosen_dir, "plugins"))
    uninstall_footer()


def do_install_claude(chosen_dir, local_repo_dir):
    install_files(FILES, os.path.join(chosen_dir, "hooks", APP_NAME), local_repo_dir)
    settings_file = os.path.join(chosen_dir, "settings.json")

    cwd = os.getcwd()
    hooks_dir = os.path.join(chosen_dir, "hooks", APP_NAME)
    if os.path.abspath(chosen_dir) == os.path.abspath(os.path.join(cwd, ".claude")):
        hook_path_prefix = f"${{CLAUDE_PROJECT_DIR}}/.claude/hooks/{APP_NAME}"
    else:
        hook_path_prefix = hooks_dir

    settings = load_json_file(settings_file) or {}
    hooks_def = {
        "UserPromptSubmit": f"{hook_path_prefix}/user_prompt_submit.py",
        "PostToolUse": f"{hook_path_prefix}/post_tool_use.py",
        "MessageDisplay": f"{hook_path_prefix}/message_display.py",
        "PreToolUse": {
            "command": f"{hook_path_prefix}/pre_tool_use.py",
            "matcher": "^Bash$",
        },
    }

    modified = register_hooks(settings.setdefault("hooks", {}), hooks_def)
    if modified or not os.path.exists(settings_file):
        if os.path.exists(settings_file):
            backup_file(settings_file)
        save_json_file(settings_file, settings)
        print("Settings configuration updated.")
    else:
        print("Configuration is already up to date (no changes needed).")

    print("\n==========================================")
    print("✓ Successfully installed llm-secret-redactor!")
    print(f"  Hooks:    {hooks_dir}")
    print(f"  Settings: {settings_file}")
    print("==========================================")


def do_install_opencode(chosen_dir, local_repo_dir):
    install_files(OPENCODE_CORE_FILES, os.path.join(chosen_dir, "secret-redactor"), local_repo_dir)
    install_files([OPENCODE_PLUGIN_FILE], os.path.join(chosen_dir, "plugins"), local_repo_dir)

    # opencode auto-discovers plugins in <config>/plugins/, so no settings
    # file modification is needed.
    print("\n==========================================")
    print("✓ Successfully installed llm-secret-redactor for opencode!")
    print(f"  Plugin: {os.path.join(chosen_dir, 'plugins', OPENCODE_PLUGIN_FILE)}")
    print("  Restart opencode to load the plugin.")
    print("==========================================")


def do_install_codex(chosen_dir, local_repo_dir):
    hooks_dir = os.path.join(chosen_dir, "hooks", APP_NAME)
    install_files(CODEX_FILES, hooks_dir, local_repo_dir)

    hooks_file = os.path.join(chosen_dir, "hooks.json")
    config = load_json_file(hooks_file) or {}
    hooks_def = {
        "UserPromptSubmit": f'python3 "{hooks_dir}/user_prompt_submit.py"',
        "PostToolUse": f'python3 "{hooks_dir}/post_tool_use.py" --host codex',
        "PreToolUse": {
            "command": f'python3 "{hooks_dir}/pre_tool_use.py" --host codex',
            "matcher": "^Bash$",
        },
        "Stop": f'python3 "{hooks_dir}/stop_reveal.py"',
    }

    modified = register_hooks(config.setdefault("hooks", {}), hooks_def)
    if modified or not os.path.exists(hooks_file):
        if os.path.exists(hooks_file):
            backup_file(hooks_file)
        save_json_file(hooks_file, config)
        print("Hooks configuration updated.")
    else:
        print("Configuration is already up to date (no changes needed).")

    print("\n==========================================")
    print("✓ Successfully installed llm-secret-redactor for Codex!")
    print(f"  Hooks:     {hooks_dir}")
    print(f"  Hooksfile: {hooks_file}")
    print("  Open Codex and run /hooks to review and trust the new hooks.")
    print("==========================================")


TOOL_NAMES = ["claude", "opencode", "codex"]

# Every tool gets a pair of symmetric entry points: do_install_<tool> and
# do_uninstall_<tool>, dispatched from here.
INSTALLERS = {
    "claude": do_install_claude,
    "opencode": do_install_opencode,
    "codex": do_install_codex,
}
UNINSTALLERS = {
    "claude": do_uninstall_claude,
    "opencode": do_uninstall_opencode,
    "codex": do_uninstall_codex,
}


def default_base_dir(tool, scope_idx):
    if tool == "opencode":
        return os.path.expanduser("~/.config/opencode") if scope_idx == 1 else os.path.abspath(".opencode")
    if tool == "codex":
        return os.path.expanduser("~/.codex") if scope_idx == 1 else os.path.abspath(".codex")
    return os.path.expanduser("~/.claude") if scope_idx == 1 else os.path.abspath(".claude")


def main():
    print("==========================================")
    print("  LLM Secret Redactor for Claude/opencode/Codex")
    print("==========================================")
    print("")

    # Determine caller directory for local files if invoked as script
    caller_dir = None
    if "__file__" in globals() and os.path.isfile(__file__):
        caller_dir = os.path.dirname(os.path.abspath(__file__))

    # Determine action
    if "--uninstall" in sys.argv or "uninstall" in sys.argv:
        action = "uninstall"
    elif "--install" in sys.argv or "install" in sys.argv:
        action = "install"
    else:
        action_options = [
            ("Install", "Install or update LLM secret redactor hooks"),
            ("Uninstall", "Cleanly remove redactor hooks and files")
        ]
        action_idx = prompt_menu("Select action", action_options)
        action = "uninstall" if action_idx == 1 else "install"

    # Determine target tool
    tool_options = [
        ("Claude Code", "Install hooks for Claude Code (.claude/settings.json)"),
        ("opencode", "Install plugin for opencode (.opencode/plugins)"),
        ("Codex", "Install hooks for OpenAI Codex CLI (.codex/hooks.json)")
    ]
    tool_idx = prompt_menu("Select target tool", tool_options)
    tool = TOOL_NAMES[tool_idx] if 0 <= tool_idx < len(TOOL_NAMES) else "claude"

    # Determine scope
    scope_options = [
        ("Local", f"Current project: applies only to this directory"),
        ("Global", f"User-wide: applies to all {tool} sessions on this system")
    ]
    scope_idx = prompt_menu("Select scope", scope_options)
    base_dir = default_base_dir(tool, scope_idx)
    scope_name = "Global" if scope_idx == 1 else "Local"

    print(f"\nSelected action: {action.upper()}")
    print(f"Selected tool:   {tool}")
    print(f"Selected scope:  {scope_name}")
    print(f"Target path:     {base_dir}\n")

    chosen_dir = prompt_input("Enter destination directory (press Enter for default)", base_dir)
    chosen_dir = os.path.abspath(os.path.expanduser(chosen_dir))

    if action == "uninstall":
        UNINSTALLERS[tool](chosen_dir)
    else:
        INSTALLERS[tool](chosen_dir, caller_dir)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
