#!/usr/bin/env python3
import sys
import os
import json
import shutil
import urllib.request
from datetime import datetime

APP_NAME = "secret-redactor"
FILES = [
    "vault.py",
    "user_prompt_submit.py",
    "post_tool_use.py",
    "message_display.py",
    "pre_tool_use.py"
]
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
            os.path.join(local_repo_dir, filename)
        ]
        for p in candidate_paths:
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()

    # 2. Remote download
    urls = [
        f"{REPO_RAW_URL}/src/secret_redactor/{filename}",
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

def do_uninstall(chosen_dir):
    print(f"\nUninstalling LLM Secret Redactor from {chosen_dir}...")
    settings_file = os.path.join(chosen_dir, "settings.json")
    hooks_dir = os.path.join(chosen_dir, "hooks", APP_NAME)

    # 1. Clean settings.json
    if os.path.isfile(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            settings = {}

        hooks = settings.get("hooks", {})
        modified = False
        file_names = set(FILES)

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
            print("No redactor hooks found in settings.json")

    # 2. Remove hooks directory
    if os.path.isdir(hooks_dir):
        shutil.rmtree(hooks_dir)
        print(f"Removed dedicated directory: {hooks_dir}")

    # Remove parent hooks/ if empty
    parent_hooks = os.path.join(chosen_dir, "hooks")
    if os.path.isdir(parent_hooks) and not os.listdir(parent_hooks):
        os.rmdir(parent_hooks)

    print("\n==========================================")
    print("✓ Uninstallation complete!")
    print("==========================================")

def do_install(chosen_dir, local_repo_dir):
    hooks_dir = os.path.join(chosen_dir, "hooks", APP_NAME)
    settings_file = os.path.join(chosen_dir, "settings.json")
    os.makedirs(hooks_dir, exist_ok=True)

    print(f"\nInstalling hook scripts into {hooks_dir}...")
    for filename in FILES:
        content = get_script_source(filename, local_repo_dir)
        target_path = os.path.join(hooks_dir, filename)
        with open(target_path, "wb") as f:
            f.write(content)
        os.chmod(target_path, 0o755)

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
            print(f"Backed up existing settings to {backup_file}")
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        print("Settings configuration updated.")
    else:
        print("Configuration is already up to date (no changes needed).")

    print("\n==========================================")
    print("✓ Successfully installed llm-secret-redactor!")
    print(f"  Hooks:    {hooks_dir}")
    print(f"  Settings: {settings_file}")
    print("==========================================")

def main():
    print("==========================================")
    print("    LLM Secret Redactor for Claude Code   ")
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

    # Determine scope
    scope_options = [
        ("Local", "Current project (.claude): applies only to this directory"),
        ("Global", "User-wide (~/.claude): applies to all Claude Code sessions")
    ]
    scope_idx = prompt_menu("Select scope", scope_options)
    if scope_idx == 1:
        default_base_dir = os.path.expanduser("~/.claude")
        scope_name = "Global"
    else:
        default_base_dir = os.path.abspath(".claude")
        scope_name = "Local"

    print(f"\nSelected action: {action.upper()}")
    print(f"Selected scope:  {scope_name}")
    print(f"Target path:     {default_base_dir}\n")

    chosen_dir = prompt_input("Enter destination directory (press Enter for default)", default_base_dir)
    chosen_dir = os.path.abspath(os.path.expanduser(chosen_dir))

    if action == "uninstall":
        do_uninstall(chosen_dir)
    else:
        do_install(chosen_dir, caller_dir)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
