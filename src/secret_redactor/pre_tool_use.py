#!/usr/bin/env python3
import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redaction import TOKEN_RE
from vault import restore_tool_input


def decision(permission, reason, updated_input=None):
    output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": permission,
        "permissionDecisionReason": reason,
    }
    if updated_input is not None:
        output["updatedInput"] = updated_input
    return {"hookSpecificOutput": output}


def restore_output(tool_name, response):
    policy = response.get("decision", "ask")
    if policy == "deny":
        return decision(
            "deny",
            f"secret-redactor: {tool_name} may not receive real secret values. "
            "Change the restore policy to allow it.",
        )
    if policy == "ask":
        return decision(
            "ask",
            f"secret-redactor: {tool_name} will run with real secret values in place of masked tokens.",
            response["value"],
        )
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": response["value"]}}


def main():
    raw_input = None
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.exit(0)

        data = json.loads(raw_input)
        session_id = data["session_id"]
        tool_input = data.get("tool_input")

        if tool_input is None:
            sys.exit(0)

        tool_name = data.get("tool_name", "")
        response = restore_tool_input(tool_input, session_id, tool_name)

        if response.get("changed"):
            print(json.dumps(restore_output(tool_name, response)))
        else:
            print(json.dumps({}))

    except Exception as exc:
        # Fail closed only when the input references masked values; without
        # the broker they cannot be restored.
        if TOKEN_RE.search(raw_input or ""):
            print(json.dumps(decision(
                "deny",
                f"secret-redactor: cannot restore masked values right now ({exc}).",
            )))
        else:
            sys.exit(0)

if __name__ == "__main__":
    main()
