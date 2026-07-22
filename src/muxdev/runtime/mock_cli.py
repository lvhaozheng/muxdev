"""Tiny interactive CLI used to exercise terminal/session behavior offline."""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    session = os.environ.get("MUXDEV_SESSION_ID", "standalone")
    print(json.dumps({"type": "ready", "session_id": session}), flush=True)
    for raw in sys.stdin:
        value = raw.rstrip("\r\n")
        if value in {"exit", "/exit"}:
            print(json.dumps({"type": "exit", "session_id": session}), flush=True)
            return 0
        if value.startswith("/report "):
            print(value.removeprefix("/report "), flush=True)
            continue
        print(f"mock:{value}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
