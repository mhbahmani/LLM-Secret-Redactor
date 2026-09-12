#!/usr/bin/env python3
"""Generic CLI over the redaction core.

Every non-Claude adapter (opencode plugin, future tools) talks to this CLI
instead of reimplementing patterns/masking logic, keeping a single source of
truth in vault.py.

Usage:
    echo '{"session_id": "s", "data": "<text or nested JSON>"}' | cli.py mask
    echo '{"session_id": "s", "data": "<text or nested JSON>"}' | cli.py unmask
    echo '{"data": "<text>"}' | cli.py check
"""
import sys
import json
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault import mask_recursive, unmask_recursive, unmask_text, SECRET_PATTERNS


def detect_secret_type(text) -> str:
    if not isinstance(text, str):
        return ""
    for regex, kind in SECRET_PATTERNS:
        match = regex.search(text)
        if match and not (match.group(0).startswith("__MASKED_") and match.group(0).endswith("__")):
            return kind
    return ""


def main():
    parser = argparse.ArgumentParser(description="llm-secret-redactor core CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("mask", help="Mask secrets in data (stdin JSON: {session_id, data})")
    sub.add_parser("unmask", help="Restore masked tokens in data (stdin JSON: {session_id, data})")
    sub.add_parser("check", help="Detect secret type in text (stdin JSON: {data})")
    reveal = sub.add_parser("reveal", help="Reveal masked tokens in raw stdin text (for humans)")
    reveal.add_argument("--session", default=None, help="Restrict to one session's vault")
    args = parser.parse_args()

    if args.command == "reveal":
        # Raw stdin text -> unmasked text (human-facing convenience)
        sys.stdout.write(unmask_text(sys.stdin.read(), session_id=args.session))
        return

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    if args.command == "check":
        print(json.dumps({"secret_type": detect_secret_type(payload.get("data"))}))
        return

    session_id = payload.get("session_id") or "default"
    data = payload.get("data")
    if args.command == "mask":
        result, changed = mask_recursive(data, session_id)
    else:
        result, changed = unmask_recursive(data, session_id)
    print(json.dumps({"data": result, "changed": changed}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail open: adapters must never crash the host tool
        print(json.dumps({"data": None, "changed": False, "error": True}))
