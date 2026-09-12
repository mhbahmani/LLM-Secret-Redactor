#!/usr/bin/env python3
import sys
import json
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault import mask_recursive

REDACTED_NOTICE = "[llm-secret-redactor] Raw secrets were masked before reaching the model. Redacted tool output:"


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", choices=("claude", "codex"), default="claude")
    args = parser.parse_args()

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

        if not changed:
            # Nothing modified
            print(json.dumps({}))
            return

        if args.host == "codex":
            # Codex cannot rewrite tool output, but a PostToolUse block makes
            # the host feed the model ONLY the "reason" instead of the raw
            # result, so we return the masked output as that reason. The raw
            # output remains visible in the local TUI for the human.
            masked_text = (
                masked_response
                if isinstance(masked_response, str)
                else json.dumps(masked_response, ensure_ascii=False)
            )
            print(json.dumps({
                "decision": "block",
                "reason": f"{REDACTED_NOTICE}\n{masked_text}",
            }))
        else:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "updatedToolOutput": masked_response
                }
            }
            print(json.dumps(output))

    except Exception:
        # Never crash the host on hook error
        sys.exit(0)

if __name__ == "__main__":
    main()
