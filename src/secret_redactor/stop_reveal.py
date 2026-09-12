#!/usr/bin/env python3
"""Codex Stop-hook adapter: reveal-on-display.

Codex has no display hook, and a tool result / assistant message is a single
channel shared with the model - so the model only ever sees __MASKED_*__.
This hook runs at the end of every turn, takes the assistant's reply, restores
masked tokens from the session vault and surfaces the revealed text to the
HUMAN as a systemMessage in the TUI. The model never sees this output.
"""
import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault import unmask_text


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
        message = data.get("last_assistant_message")
        if not isinstance(message, str) or not message:
            sys.exit(0)

        session_id = data.get("session_id") or "default"
        revealed_lines = []
        for line in message.splitlines():
            revealed = unmask_text(line, session_id)
            if revealed != line:
                revealed_lines.append(revealed)

        if revealed_lines:
            body = "\n".join(revealed_lines)
            print(json.dumps({
                "systemMessage": f"Revealed secrets (llm-secret-redactor):\n{body}"
            }))
    except Exception:
        # Never crash the host on hook error
        sys.exit(0)


if __name__ == "__main__":
    main()
