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

# Use python to do reliable TTY arrow-key selection
select_scope_interactive() {
    python3 - <<'PYEOF'
import sys, os, tty, termios

options = [
    ("Local", "Current project (.claude): applies only to this directory"),
    ("Global", "User-wide (~/.claude): applies to all Claude Code sessions")
]

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
# If no tty available, fallback to default 0 (Local)
if fd is None:
    print(0)
    sys.exit(0)

old_settings = termios.tcgetattr(fd)
tty_out = os.fdopen(os.dup(fd), 'w')

selected = 0

def render(first=False):
    if not first:
        # Move up 2 lines and to column 1
        tty_out.write(f"\033[{len(options)}A\r")
    for i, (name, desc) in enumerate(options):
        # Clear line
        tty_out.write("\033[2K\r")
        if i == selected:
            tty_out.write(f"  \033[1;32m❯ {name:<7}\033[0m - {desc}\n")
        else:
            tty_out.write(f"    {name:<7} - {desc}\n")
    tty_out.flush()

try:
    # Hide cursor
    tty_out.write("\033[?25l")
    tty_out.write("Select installation scope (use ↑/↓ arrow keys, Enter to confirm):\n")
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
                selected = (selected - 1) % len(options)
                render()
            elif seq == b'[B': # Down
                selected = (selected + 1) % len(options)
                render()
        elif ch in (b'k', b'K'):
            selected = (selected - 1) % len(options)
            render()
        elif ch in (b'j', b'J'):
            selected = (selected + 1) % len(options)
            render()

finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    tty_out.write("\033[?25h\n")
    tty_out.flush()

# Output selection index to stdout for bash
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

SELECTED_INDEX=$(select_scope_interactive)

case "$SELECTED_INDEX" in
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
echo "Selected scope: $SCOPE_NAME"
echo "Configuration and hooks will be installed inside:"
echo "  Directory: $DEFAULT_BASE_DIR"
echo "  Hooks:     $DEFAULT_BASE_DIR/hooks"
echo "  Settings:  $DEFAULT_BASE_DIR/settings.json"
echo ""

prompt_read "Enter destination directory (press Enter for default)" CHOSEN_DIR "$DEFAULT_BASE_DIR"

# Expand tilde if user typed ~/...
CHOSEN_DIR="${CHOSEN_DIR/#\~/$HOME}"
mkdir -p "$CHOSEN_DIR/hooks"

HOOKS_DIR="$CHOSEN_DIR/hooks"
SETTINGS_FILE="$CHOSEN_DIR/settings.json"
REPO_RAW_URL="${REPO_RAW_URL:-https://raw.githubusercontent.com/mhbahmani/llm-secret-redactor/master}"
FILES=("vault.py" "user_prompt_submit.py" "post_tool_use.py" "message_display.py" "pre_tool_use.py")

echo ""
echo "Installing hook scripts into $HOOKS_DIR..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"

for file in "${FILES[@]}"; do
    if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/$file" ]; then
        cp "$SCRIPT_DIR/$file" "$HOOKS_DIR/$file"
    else
        curl -fsSL "$REPO_RAW_URL/$file" -o "$HOOKS_DIR/$file"
    fi
    chmod +x "$HOOKS_DIR/$file"
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
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{settings_path}.backup_{ts}"
        shutil.copy2(settings_path, backup_path)
        print(f"Backed up existing settings to {backup_path}")
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

for event, cmd in hooks_def.items():
    event_list = hooks.setdefault(event, [])
    
    already_registered = False
    for group in event_list:
        for h in group.get("hooks", []):
            if h.get("command") == cmd or os.path.basename(str(h.get("command", ""))) == os.path.basename(cmd):
                h["command"] = cmd
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

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2)

PYEOF

echo ""
echo "=========================================="
echo "✓ Successfully installed llm-secret-redactor!"
echo "  Hooks:    $HOOKS_DIR"
echo "  Settings: $SETTINGS_FILE"
echo "=========================================="
