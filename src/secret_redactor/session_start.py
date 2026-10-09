#!/usr/bin/env python3
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vault import broker_stats


def main():
    try:
        json.loads(sys.stdin.read() or "{}")
        broker_stats()
        print(json.dumps({}))
    except Exception:
        # A later masking hook retries startup and fails closed if unavailable.
        sys.exit(0)


if __name__ == "__main__":
    main()
