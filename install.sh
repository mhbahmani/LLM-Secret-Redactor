#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-$PWD}"
CLAUDE_DIR="$TARGET_DIR/.claude"
HOOKS_DIR="$CLAUDE_DIR/hooks"
REPO_RAW_URL="${REPO_RAW_URL:-https://raw.githubusercontent.com/username/llm-secret-redactor/main}"

mkdir -p "$HOOKS_DIR"

FILES=("vault.py" "user_prompt_submit.py" "post_tool_use.py" "message_display.py" "pre_tool_use.py")

# Download or copy scripts
for file in "${FILES[@]}"; do
    if [ -f "$(dirname "$0")/$file" ]; then
        cp "$(dirname "$0")/$file" "$HOOKS_DIR/$file"
    else
        curl -fsSL "$REPO_RAW_URL/$file" -o "$HOOKS_DIR/$file"
    fi
    chmod +x "$HOOKS_DIR/$file"
done

# Write .claude/settings.json
SETTINGS_FILE="$CLAUDE_DIR/settings.json"
cat <<'EOF' > "$SETTINGS_FILE"
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/user_prompt_submit.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/post_tool_use.py"
          }
        ]
      }
    ],
    "MessageDisplay": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/message_display.py"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/pre_tool_use.py"
          }
        ]
      }
    ]
  }
}
EOF

echo "✓ llm-secret-redactor installed in $CLAUDE_DIR"
