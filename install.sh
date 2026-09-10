#!/usr/bin/env bash
set -euo pipefail

# Download or run installer via python3 directly
PYTHON_CMD="python3"
if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    echo "Error: python3 is required for installation." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/install.py" ]; then
    exec "$PYTHON_CMD" "$SCRIPT_DIR/install.py" "$@"
fi

INSTALLER_URL="${INSTALLER_URL:-https://raw.githubusercontent.com/mhbahmani/llm-secret-redactor/master/install.py}"
TEMP_SCRIPT="$(mktemp /tmp/secret_redactor_install.XXXXXX.py)"
trap 'rm -f "$TEMP_SCRIPT"' EXIT

curl -fsSL "$INSTALLER_URL" -o "$TEMP_SCRIPT"

# If /dev/tty exists and is readable, connect stdin to it for true interactivity
if (exec 4</dev/tty) 2>/dev/null; then
    exec 4<&-
    exec "$PYTHON_CMD" "$TEMP_SCRIPT" "$@" </dev/tty
else
    exec "$PYTHON_CMD" "$TEMP_SCRIPT" "$@"
fi
