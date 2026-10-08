#!/usr/bin/env python3
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault import clear_session


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
        clear_session(data["session_id"])
        print(json.dumps({}))
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
