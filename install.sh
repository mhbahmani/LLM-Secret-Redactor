#!/usr/bin/env bash
set -euo pipefail

# Terminal interactive input even when piped via `curl ... | bash`
exec 3<&0
IS_TTY=0
if [ -t 0 ]; then
    IS_TTY=1
elif (exec 4</dev/tty) 2>/dev/null; then
    exec 3</dev/tty
    exec 4<&-
    IS_TTY=1
fi

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

# Interactive arrow key selector
select_option_menu() {
    local prompt_title="$1"
    shift
    local options=("$@")
    local selected=0
    local count=${#options[@]}

    # Fallback to simple prompt if non-interactive environment
    if [ "$IS_TTY" -eq 0 ]; then
        echo "$prompt_title" >&2
        for i in "${!options[@]}"; do
            echo "  $((i+1))) ${options[i]}" >&2
        done
        local num_choice
        prompt_read "Choose option (1-$count)" num_choice "1"
        local idx=$((num_choice - 1))
        if [ "$idx" -lt 0 ] || [ "$idx" -ge "$count" ]; then
            idx=0
        fi
        echo "$idx"
        return
    fi

    # Save terminal settings and hide cursor
    local old_stty
    old_stty=$(stty -g 2>/dev/null || true)
    stty -icanon -echo min 1 time 0 2>/dev/null || true
    tput civis 2>/dev/null || printf "\033[?25l" >&2

    cleanup_menu() {
        tput cnorm 2>/dev/null || printf "\033[?25h" >&2
        if [ -n "$old_stty" ]; then
            stty "$old_stty" 2>/dev/null || true
        fi
    }
    trap cleanup_menu EXIT INT TERM

    echo "$prompt_title (use ↑/↓ arrow keys, Enter to select):" >&2
    for ((i=0; i<count; i++)); do
        echo "" >&2
    done

    render_menu() {
        # Move up 'count' lines
        for ((i=0; i<count; i++)); do
            tput cuu1 2>/dev/null || printf "\033[1A" >&2
        done
        for ((i=0; i<count; i++)); do
            # Clear line
            tput el 2>/dev/null || printf "\033[2K" >&2
            if [ "$i" -eq "$selected" ]; then
                printf "  \033[1;32m❯ %s\033[0m\n" "${options[i]}" >&2
            else
                printf "    %s\n" "${options[i]}" >&2
            fi
        done
    }

    render_menu

    while true; do
        local key=""
        key=$(dd bs=1 count=1 2>/dev/null <&3 || true)
        if [ "$key" = $'\x1b' ]; then
            local rest=""
            rest=$(dd bs=1 count=2 2>/dev/null <&3 || true)
            case "$rest" in
                "[A") # Up
                    selected=$(( (selected - 1 + count) % count ))
                    render_menu
                    ;;
                "[B") # Down
                    selected=$(( (selected + 1) % count ))
                    render_menu
                    ;;
            esac
        elif [ "$key" = "" ] || [ "$key" = $'\n' ] || [ "$key" = $'\r' ]; then
            break
        elif [ "$key" = "k" ] || [ "$key" = "K" ]; then
            selected=$(( (selected - 1 + count) % count ))
            render_menu
        elif [ "$key" = "j" ] || [ "$key" = "J" ]; then
            selected=$(( (selected + 1) % count ))
            render_menu
        fi
    done

    cleanup_menu
    trap - EXIT INT TERM
    echo "$selected"
}

echo "=========================================="
echo "    LLM Secret Redactor for Claude Code   "
echo "=========================================="
echo ""

MENU_OPTIONS=(
    "Local  (current project: applies only to this directory)"
    "Global (user-wide: ~/.claude, applies to all Claude Code sessions)"
)

SELECTED_INDEX=$(select_option_menu "Select installation scope" "${MENU_OPTIONS[@]}")

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
