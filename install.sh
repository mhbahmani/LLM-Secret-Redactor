#!/usr/bin/env bash
set -euo pipefail

# Terminal interactive input even when piped via `curl ... | bash`
exec 3<&0
if [ ! -t 0 ]; then
    if (exec 4</dev/tty) 2>/dev/null; then
        exec 3</dev/tty
        exec 4<&-
    fi
fi

FILES=("vault.py" "user_prompt_submit.py" "post_tool_use.py" "message_display.py" "pre_tool_use.py")
REPO_RAW_URL="${REPO_RAW_URL:-https://raw.githubusercontent.com/mhbahmani/llm-secret-redactor/master}"

# Interactive arrow-key selection menu using Python
select_menu_interactive() {
    local prompt_title="$1"
    shift
    local raw_items=("$@")

    python3 - "${prompt_title}" "${raw_items[@]}" <<'PYEOF'
import sys, os, tty, termios

title = sys.argv[1]
items = []
for arg in sys.argv[2:]:
    parts = arg.split("|", 1)
    items.append((parts[0], parts[1] if len(parts) > 1 else ""))

def get_tty_fd():
    for fd_candidate in (3, 0):
        try:
            if os.isatty(fd_candidate):
                return fd_candidate
        except Exception:
            pass
    try:
        fd_tty = os.open("/dev/tty", os.O_RDWR)
        if os.isatty(fd_tty):
            return fd_tty
    except Exception:
        pass
    return None

fd = get_tty_fd()
if fd is None:
    print(0)
    sys.exit(0)

old_settings = termios.tcgetattr(fd)
tty_out = os.fdopen(os.dup(fd), 'w')
selected = 0

def render(first=False):
    if not first:
        tty_out.write(f"\033[{len(items)}A\r")
    for i, (name, desc) in enumerate(items):
        tty_out.write("\033[2K\r")
        if i == selected:
            if desc:
                tty_out.write(f"  \033[1;32m❯ {name:<10}\033[0m - {desc}\n")
            else:
                tty_out.write(f"  \033[1;32m❯ {name}\033[0m\n")
        else:
            if desc:
                tty_out.write(f"    {name:<10} - {desc}\n")
            else:
                tty_out.write(f"    {name}\n")
    tty_out.flush()

try:
    tty_out.write("\033[?25l")
    tty_out.write(f"{title} (use ↑/↓ arrow keys, Enter to confirm):\n")
    tty_out.flush()
    render(first=True)

    tty.setraw(fd)
    while True:
        ch = os.read(fd, 1)
        if ch in (b'\r', b'\n'):
            break
        elif ch == b'\x03': # Ctrl+C
            raise KeyboardInterrupt
        elif ch == b'\x1b':
            seq = os.read(fd, 2)
            if seq == b'[A': # Up
                selected = (selected - 1) % len(items)
                render()
            elif seq == b'[B': # Down
                selected = (selected + 1) % len(items)
                render()
        elif ch in (b'k', b'K'):
            selected = (selected - 1) % len(items)
            render()
        elif ch in (b'j', b'J'):
            selected = (selected + 1) % len(items)
            render()

finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    tty_out.write("\033[?25h\n")
    tty_out.flush()

print(selected)
PYEOF
}

prompt_read() {
    local prompt_msg="$1"
    local var_name="$2"
    local default_val="${3:-}"

    if [ -n "$default_val" ]; then
        printf "%s [%s]: " "$prompt_msg" "$default_val" >&2
    else
        printf "%s: " "$prompt_msg" >&2
    fi

    local input
    read -r -u 3 input || input=""
    if [ -z "$input" ]; then
        eval "$var_name=\"\$default_val\""
    else
        eval "$var_name=\"\$input\""
    fi
}

echo "=========================================="
echo "    LLM Secret Redactor for Claude Code   "
echo "=========================================="
echo ""

# Determine action: install or uninstall
ACTION="install"
if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "uninstall" ]; then
    ACTION="uninstall"
elif [ "${1:-}" = "--install" ] || [ "${1:-}" = "install" ]; then
    ACTION="install"
else
    ACTION_MENU=(
        "Install|Install or update LLM secret redactor hooks"
        "Uninstall|Cleanly remove redactor hooks and files"
    )
    ACTION_INDEX=$(select_menu_interactive "Select action" "${ACTION_MENU[@]}")
    if [ "$ACTION_INDEX" -eq 1 ]; then
        ACTION="uninstall"
    fi
fi

# Determine scope
SCOPE_MENU=(
    "Local|Current project (.claude): applies only to this directory"
    "Global|User-wide (~/.claude): applies to all Claude Code sessions"
)
SCOPE_INDEX=$(select_menu_interactive "Select scope" "${SCOPE_MENU[@]}")

case "$SCOPE_INDEX" in
    1)
        DEFAULT_BASE_DIR="$HOME/.claude"
        SCOPE_NAME="Global"
        ;;
    *)
        DEFAULT_BASE_DIR="$PWD/.claude"
        SCOPE_NAME="Local"
        ;;
esac

echo ""
echo "Selected action: ${ACTION^^}"
echo "Selected scope:  $SCOPE_NAME"
echo "Target path:     $DEFAULT_BASE_DIR"
echo ""

prompt_read "Enter destination directory (press Enter for default)" CHOSEN_DIR "$DEFAULT_BASE_DIR"

# Expand tilde if user typed ~/...
CHOSEN_DIR="${CHOSEN_DIR/#\~/$HOME}"
HOOKS_DIR="$CHOSEN_DIR/hooks"
SETTINGS_FILE="$CHOSEN_DIR/settings.json"

# ==============================================================================
# UNINSTALL LOGIC
# ==============================================================================
if [ "$ACTION" = "uninstall" ]; then
    echo ""
    echo "Uninstalling LLM Secret Redactor from $CHOSEN_DIR..."

    # 1. Clean settings.json if exists
    if [ -f "$SETTINGS_FILE" ]; then
        python3 - <<PYEOF
import json
import os
import shutil
from datetime import datetime

settings_path = "$SETTINGS_FILE"
file_names = {'vault.py', 'user_prompt_submit.py', 'post_tool_use.py', 'message_display.py', 'pre_tool_use.py'}

try:
    with open(settings_path, "r", encoding="utf-8") as f:
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
            if basename in file_names or "claude_secret_vault" in cmd:
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
    backup_path = f"{settings_path}.backup_{ts}"
    shutil.copy2(settings_path, backup_path)
    print(f"Backed up settings before uninstallation to {backup_path}")

    # Remove hooks key if empty
    if not hooks and "hooks" in settings:
        del settings["hooks"]

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    print(f"Cleaned redactor hooks from {settings_path}")
else:
    print("No redactor hooks found in settings.json")
PYEOF
    fi

    # 2. Remove only redactor hook files
    for file in "${FILES[@]}"; do
        if [ -f "$HOOKS_DIR/$file" ]; then
            rm -f "$HOOKS_DIR/$file"
            echo "Removed $HOOKS_DIR/$file"
        fi
    done

    # Remove hooks dir only if empty
    if [ -d "$HOOKS_DIR" ] && [ -z "$(ls -A "$HOOKS_DIR")" ]; then
        rmdir "$HOOKS_DIR" 2>/dev/null || true
    fi

    echo ""
    echo "=========================================="
    echo "✓ Uninstallation complete!"
    echo "=========================================="
    exit 0
fi

# ==============================================================================
# INSTALL LOGIC (IDEMPOTENT)
# ==============================================================================
mkdir -p "$HOOKS_DIR"
echo ""
echo "Installing hook scripts into $HOOKS_DIR..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"

for file in "${FILES[@]}"; do
    TARGET_PATH="$HOOKS_DIR/$file"
    if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/$file" ]; then
        cp "$SCRIPT_DIR/$file" "$TARGET_PATH"
    else
        curl -fsSL "$REPO_RAW_URL/$file" -o "$TARGET_PATH"
    fi
    chmod +x "$TARGET_PATH"
done

# In local mode, use ${CLAUDE_PROJECT_DIR}/.claude/hooks/
# In global mode or custom path, absolute path ensures hooks run anywhere
if [ "$CHOSEN_DIR" = "$PWD/.claude" ]; then
    HOOK_PATH_PREFIX="\${CLAUDE_PROJECT_DIR}/.claude/hooks"
else
    HOOK_PATH_PREFIX="$HOOKS_DIR"
fi

python3 - <<PYEOF
import json
import os
import shutil
from datetime import datetime

settings_path = "$SETTINGS_FILE"
prefix = "$HOOK_PATH_PREFIX"

settings = {}
if os.path.exists(settings_path):
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except Exception as e:
        print(f"Warning: Could not parse existing settings.json: {e}")
        settings = {}

hooks = settings.setdefault("hooks", {})

hooks_def = {
    "UserPromptSubmit": f"{prefix}/user_prompt_submit.py",
    "PostToolUse": f"{prefix}/post_tool_use.py",
    "MessageDisplay": f"{prefix}/message_display.py",
    "PreToolUse": f"{prefix}/pre_tool_use.py"
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
                # Update existing path idempotently if changed
                if cur_cmd != cmd:
                    h["command"] = cmd
                    modified = True
                already_registered = True
                break
        if already_registered:
            break

    if not already_registered:
        event_list.append({
            "hooks": [
                {
                    "type": "command",
                    "command": cmd
                }
            ]
        })
        modified = True

if modified or not os.path.exists(settings_path):
    if os.path.exists(settings_path):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{settings_path}.backup_{ts}"
        shutil.copy2(settings_path, backup_path)
        print(f"Backed up existing settings to {backup_path}")

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    print("Settings configuration updated.")
else:
    print("Configuration is already up to date (no changes needed).")

PYEOF

echo ""
echo "=========================================="
echo "✓ Successfully installed llm-secret-redactor!"
echo "  Hooks:    $HOOKS_DIR"
echo "  Settings: $SETTINGS_FILE"
echo "=========================================="
