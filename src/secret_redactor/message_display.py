#!/usr/bin/env python3
import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault import unmask_text

def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.exit(0)

        data = json.loads(raw_input)
        session_id = data.get("session_id", "default")
        delta = data.get("delta")

        if delta is None:
            sys.exit(0)

        unmasked_delta = unmask_text(delta, session_id)

        if unmasked_delta != delta:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "MessageDisplay",
                    "displayContent": unmasked_delta
                }
            }
            print(json.dumps(output))
        else:
            print(json.dumps({}))

    except Exception:
        sys.exit(0)

if __name__ == "__main__":
    main()
