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
echo "Select installation scope:"
echo "  1) Local  (current project: applies only to this directory)"
echo "  2) Global (user-wide: ~/.claude, applies to all Claude Code sessions)"
echo ""

prompt_read "Choose option (1 or 2)" SCOPE_CHOICE "1"

case "$SCOPE_CHOICE" in
    2)
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
REPO_RAW_URL="${REPO_RAW_URL:-https://raw.githubusercontent.com/username/llm-secret-redactor/main}"
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

# In local mode, we can use ${CLAUDE_PROJECT_DIR}/.claude/hooks/
# In global mode or custom path, absolute path ensures hooks run regardless of cwd.
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
        # Create timestamped backup of existing settings
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{settings_path}.backup_{ts}"
        shutil.copy2(settings_path, backup_path)
        print(f"Backed up existing settings to {backup_path}")
    except Exception as e:
        print(f"Warning: Could not parse existing settings.json: {e}")
        settings = {}

# Ensure hooks root exists and preserve all existing top-level settings (env, permissions, etc.)
hooks = settings.setdefault("hooks", {})

hooks_def = {
    "UserPromptSubmit": f"{prefix}/user_prompt_submit.py",
    "PostToolUse": f"{prefix}/post_tool_use.py",
    "MessageDisplay": f"{prefix}/message_display.py",
    "PreToolUse": f"{prefix}/pre_tool_use.py"
}

hook_scripts = set(hooks_def.values())

for event, cmd in hooks_def.items():
    event_list = hooks.setdefault(event, [])
    
    # Check if this specific hook is already registered
    already_registered = False
    for group in event_list:
        for h in group.get("hooks", []):
            if h.get("command") == cmd or os.path.basename(str(h.get("command", ""))) == os.path.basename(cmd):
                # Update existing command path in place without duplicating
                h["command"] = cmd
                already_registered = True
                break
        if already_registered:
            break

    # If not registered, append without touching any existing user hooks
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
