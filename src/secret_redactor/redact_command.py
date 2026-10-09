#!/usr/bin/env python3
"""Handles the user-typed /redact command (UserPromptExpansion hook).

Only a command the user types reaches this hook, so the model cannot switch
masking off by itself. The command is always blocked: its text never reaches
the model, and the user sees the outcome as the block reason.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault import masking_enabled, set_masking

COMMAND = "redact"
USAGE = "Usage: /redact off | on | status"


def describe(enabled):
    if enabled:
        return "Secret masking is ON for this session."
    return (
        "Secret masking is OFF for this session: tool output and prompts reach Claude "
        "unmasked. Run /redact on to turn it back on."
    )


def handle(session_id, argument):
    if argument in ("off", "disable"):
        return describe(set_masking(session_id, False))
    if argument in ("on", "enable"):
        return describe(set_masking(session_id, True))
    if argument in ("", "status"):
        return describe(masking_enabled(session_id))
    return USAGE


def main():
    data = json.loads(sys.stdin.read() or "{}")
    if data.get("command_name") != COMMAND:
        print(json.dumps({}))
        return
    argument = (data.get("command_args") or "").strip().lower()
    try:
        message = handle(data["session_id"], argument)
    except Exception as exc:
        message = f"secret-redactor: could not change masking, it is unchanged ({exc})."
    print(json.dumps({"decision": "block", "reason": message}))


if __name__ == "__main__":
    main()
