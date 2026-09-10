#!/usr/bin/env python3
import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault import SECRET_PATTERNS

def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.exit(0)

        data = json.loads(raw_input)
        prompt = data.get("prompt", "")

        for regex, kind in SECRET_PATTERNS:
            if regex.search(prompt):
                output = {
                    "decision": "block",
                    "reason": f"Secret/token of type [{kind}] detected in prompt. Please remove it before sending."
                }
                print(json.dumps(output))
                sys.exit(0)

        # Allow prompt if no secret found
        print(json.dumps({}))

    except Exception:
        sys.exit(0)

if __name__ == "__main__":
    main()
