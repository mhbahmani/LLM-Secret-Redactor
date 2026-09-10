#!/usr/bin/env python3
import sys
import json
import os

# Ensure local directory is on python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault import mask_recursive

def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.exit(0)

        data = json.loads(raw_input)
        session_id = data.get("session_id", "default")
        tool_response = data.get("tool_response")

        if tool_response is None:
            sys.exit(0)

        masked_response, changed = mask_recursive(tool_response, session_id)

        if changed:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "updatedToolOutput": masked_response
                }
            }
            print(json.dumps(output))
        else:
            # Nothing modified
            print(json.dumps({}))

    except Exception:
        # Never crash Claude Code on hook error
        sys.exit(0)

if __name__ == "__main__":
    main()
