from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    checks = [
        [sys.executable, "-m", "pytest"],
        [sys.executable, "-m", "muxdev", "--version"],
        ["muxdev", "--version"],
        ["muxdev", "provider", "detect", "--json"],
    ]
    for command in checks:
        completed = run(command)
        if completed.returncode != 0:
            print(completed.stdout)
            print(completed.stderr, file=sys.stderr)
            return completed.returncode
        if command[-1] == "--json":
            json.loads(completed.stdout)
    return 0


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
