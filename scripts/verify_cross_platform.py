"""Run the compact cross-platform verification layers."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUICK_LIMIT_SECONDS = 45


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("quick", "full"), default="quick")
    suite = parser.parse_args().suite
    commands = [
        [sys.executable, "-m", "muxdev", "--help"],
        [sys.executable, "-m", "muxdev", "provider", "list"],
        [sys.executable, "-m", "muxdev", "config", "validate"],
        [sys.executable, "scripts/verify_docs.py"],
        [sys.executable, "-m", "ruff", "check", "src/muxdev", "scripts", "tests"],
        [sys.executable, "-m", "pytest", "-q", "tests/unit", "tests/contract"],
    ]
    if suite == "full":
        commands.extend([
            [sys.executable, "-m", "pytest", "-q", "tests/integration"],
            [sys.executable, "-m", "pytest", "-q", "tests/migration"],
            [sys.executable, "-m", "pytest", "-q", "tests/release"],
        ])
    started = time.monotonic()
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, text=True, check=False)
        if completed.returncode:
            return completed.returncode
    elapsed = time.monotonic() - started
    if suite == "quick" and elapsed > QUICK_LIMIT_SECONDS:
        print(f"quick suite exceeded {QUICK_LIMIT_SECONDS}s: {elapsed:.2f}s", file=sys.stderr)
        return 1
    print(f"{suite} verification passed in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
