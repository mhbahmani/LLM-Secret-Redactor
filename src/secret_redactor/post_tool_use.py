#!/usr/bin/env python3
import sys
import json
import os

# Ensure local directory is on python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redaction import load_patterns, redact, transform_value


def output(updated):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": updated,
        }
    }


def redact_without_broker(tool_response):
    """Irreversible fallback that keeps the output shape Claude Code expects."""
    patterns = load_patterns()
    return transform_value(
        tool_response,
        lambda text: redact(text, patterns, lambda kind, _secret: f"[REDACTED_{kind}]"),
    )


def main():
    raw_input = sys.stdin.read()
    if not raw_input:
        sys.exit(0)

    data = json.loads(raw_input)
    session_id = data["session_id"]
    tool_response = data.get("tool_response")

    if tool_response is None:
        sys.exit(0)

    try:
        from vault import mask_recursive
        masked_response, changed = mask_recursive(tool_response, session_id)
    except Exception:
        # Fail closed: without the broker the output can still be redacted,
        # it just cannot be restored later.
        masked_response = redact_without_broker(tool_response)
        changed = masked_response != tool_response

    print(json.dumps(output(masked_response) if changed else {}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"secret-redactor: could not redact tool output: {exc}", file=sys.stderr)
        sys.exit(2)
