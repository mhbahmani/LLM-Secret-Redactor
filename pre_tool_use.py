#!/usr/bin/env python3
import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault import unmask_recursive

def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.exit(0)

        data = json.loads(raw_input)
        session_id = data.get("session_id", "default")
        tool_input = data.get("tool_input")

        if tool_input is None:
            sys.exit(0)

        unmasked_input, changed = unmask_recursive(tool_input, session_id)

        if changed:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": unmasked_input
                }
            }
            print(json.dumps(output))
        else:
            print(json.dumps({}))

    except Exception:
        sys.exit(0)

if __name__ == "__main__":
    main()
