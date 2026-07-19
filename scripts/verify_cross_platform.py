from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / ".test_workspaces" / "test-logs"
PYTEST_CACHE_DIR = ROOT / ".test_workspaces" / ".pytest_cache" / "v" / "cache"
FULL_SUITE_LIMIT_SECONDS = 6 * 60


def main() -> int:
    parser = argparse.ArgumentParser(description="Run muxdev's cross-platform verification baseline.")
    parser.add_argument("--suite", choices=("quick", "full"), default="quick")
    args = parser.parse_args()

    checks = [
        [sys.executable, "-m", "muxdev", "--version"],
        ["muxdev", "provider", "detect", "--json"],
        ["muxdev", "demo", "--scenario", "trusted-delivery-v1", "--mode", "replay", "--json"],
        ["muxdev", "benchmark", "plan", "trusted-routing-v1", "--json"],
        [sys.executable, "scripts/verify_docs.py"],
    ]
    pytest_command = [sys.executable, "-m", "pytest", "-q"]
    if args.suite == "quick":
        pytest_command.extend(["-m", "not integration and not release"])
        checks.append(pytest_command)
    else:
        # Keep files intact but run two isolated pytest processes. This avoids
        # sharing in-process globals while meeting the six-minute integration
        # feedback gate on two-core Windows/Linux CI runners.
        pytest_commands = full_suite_shards()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Pre-creating the cache tree avoids an atomic-directory creation failure
    # observed in restricted Windows runners while keeping all cache files local.
    PYTEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{args.suite}.log"
    log_parts: list[str] = []
    for command in checks:
        completed = run(command)
        log_parts.extend(
            [
                f"$ {' '.join(command)}",
                completed.stdout,
                completed.stderr,
                f"exit={completed.returncode}",
                "",
            ]
        )
        if completed.returncode != 0:
            log_path.write_text("\n".join(log_parts), encoding="utf-8")
            print(completed.stdout)
            print(completed.stderr, file=sys.stderr)
            return completed.returncode
        if command[-1] == "--json":
            json.loads(completed.stdout)
    if args.suite == "full":
        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            completed_shards = list(executor.map(run, pytest_commands))
        elapsed = time.monotonic() - started
        for index, (command, completed) in enumerate(zip(pytest_commands, completed_shards, strict=True), start=1):
            log_parts.extend(
                [
                    f"$ shard-{index}: {' '.join(command)}",
                    completed.stdout,
                    completed.stderr,
                    f"exit={completed.returncode}",
                    "",
                ]
            )
        failed = next((completed for completed in completed_shards if completed.returncode != 0), None)
        if failed is not None or elapsed > FULL_SUITE_LIMIT_SECONDS:
            log_parts.append(f"full-suite-seconds={elapsed:.2f}; limit={FULL_SUITE_LIMIT_SECONDS}")
            log_path.write_text("\n".join(log_parts), encoding="utf-8")
            if failed is not None:
                print(failed.stdout)
                print(failed.stderr, file=sys.stderr)
                return failed.returncode
            print(f"full suite exceeded {FULL_SUITE_LIMIT_SECONDS}s: {elapsed:.2f}s", file=sys.stderr)
            return 1
        log_parts.append(f"full-suite-seconds={elapsed:.2f}; limit={FULL_SUITE_LIMIT_SECONDS}")
    log_path.write_text("\n".join(log_parts), encoding="utf-8")
    print(f"{args.suite} verification passed; log={log_path.relative_to(ROOT)}")
    return 0


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
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


def full_suite_shards() -> list[list[str]]:
    """Balance complete test files across two isolated pytest processes."""
    files = sorted((ROOT / "tests").glob("test_*.py"), key=lambda path: (-path.stat().st_size, path.name))
    shards: list[list[Path]] = [[], []]
    weights = [0, 0]
    for path in files:
        target = 0 if weights[0] <= weights[1] else 1
        shards[target].append(path)
        weights[target] += path.stat().st_size
    commands: list[list[str]] = []
    for index, shard in enumerate(shards, start=1):
        commands.append(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-m",
                "not release",
                "-o",
                "addopts=",
                "--basetemp",
                f".test_workspaces/pytest-shard-{index}",
                "-o",
                f"cache_dir=.test_workspaces/.pytest_cache/shard-{index}",
                *[str(path.relative_to(ROOT)) for path in shard],
            ]
        )
    return commands


if __name__ == "__main__":
    raise SystemExit(main())
