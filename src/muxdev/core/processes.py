"""Cross-platform lifecycle control for every muxdev child process."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .platforms import hidden_subprocess_kwargs, is_windows, script_invocation
from .redaction import redact


DEFAULT_OUTPUT_LIMIT = 1_000_000


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    cancelled: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class ProcessSupervisor:
    """Own process groups and make timeout/cancellation cleanup observable."""

    _lock = threading.RLock()
    _processes: dict[str, dict[int, subprocess.Popen[str]]] = {}
    _external_pids: dict[str, set[int]] = {}
    _cancelled_runs: set[str] = set()

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        run_id: str,
        stage_id: str,
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 300,
        output_limit: int = DEFAULT_OUTPUT_LIMIT,
    ) -> ProcessResult:
        del stage_id
        if not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValueError("process argv must be a non-empty string sequence")
        invocation = script_invocation(str(argv[0]), tuple(str(item) for item in argv[1:]))
        started = time.monotonic()
        process = subprocess.Popen(
            invocation,
            cwd=cwd,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(env) if env is not None else None,
            start_new_session=not is_windows(),
            **hidden_subprocess_kwargs(new_process_group=True),
        )
        self._register(run_id, process)
        timed_out = False
        try:
            stdout, stderr = process.communicate(stdin, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_tree(process.pid, grace_seconds=2.0)
            stdout, stderr = process.communicate()
            stderr = (stderr or "") + "\nProcess timed out."
        finally:
            self._unregister(run_id, process.pid)
        with self._lock:
            cancelled = run_id in self._cancelled_runs
        stdout, stdout_truncated = _bounded(redact(stdout or ""), output_limit)
        stderr, stderr_truncated = _bounded(redact(stderr or ""), output_limit)
        return ProcessResult(
            argv=tuple(argv),
            returncode=124 if timed_out else int(process.returncode or 0),
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=timed_out,
            cancelled=cancelled,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    def cancel(self, run_id: str) -> tuple[int, ...]:
        """Idempotently terminate the complete process tree for a run."""
        with self._lock:
            self._cancelled_runs.add(run_id)
            processes = tuple(self._processes.get(run_id, {}).values())
            external_pids = tuple(self._external_pids.get(run_id, set()))
        for process in processes:
            if process.poll() is None:
                self._terminate_tree(process.pid, grace_seconds=2.0)
        for pid in external_pids:
            self._terminate_external_tree(pid, grace_seconds=2.0)
        return tuple(process.pid for process in processes) + external_pids

    @classmethod
    def active_pids(cls, run_id: str) -> tuple[int, ...]:
        with cls._lock:
            owned = tuple(
                pid
                for pid, process in cls._processes.get(run_id, {}).items()
                if process.poll() is None
            )
            external = tuple(
                pid for pid in cls._external_pids.get(run_id, set()) if _pid_exists(pid)
            )
            return owned + external

    @classmethod
    def register_external(cls, run_id: str, process: subprocess.Popen[str]) -> None:
        cls._register(run_id, process)

    @classmethod
    def unregister_external(cls, run_id: str, pid: int) -> None:
        cls._unregister(run_id, pid)

    @classmethod
    def register_external_pid(cls, run_id: str, pid: int) -> None:
        with cls._lock:
            if run_id in cls._cancelled_runs:
                cls._terminate_external_tree(pid, grace_seconds=0)
                raise RuntimeError(f"run {run_id} is already cancelled")
            cls._external_pids.setdefault(run_id, set()).add(pid)

    @classmethod
    def unregister_external_pid(cls, run_id: str, pid: int) -> None:
        with cls._lock:
            pids = cls._external_pids.get(run_id)
            if not pids:
                return
            pids.discard(pid)
            if not pids:
                cls._external_pids.pop(run_id, None)

    @classmethod
    def _register(cls, run_id: str, process: subprocess.Popen[str]) -> None:
        with cls._lock:
            if run_id in cls._cancelled_runs:
                cls._terminate_tree(process.pid, grace_seconds=0)
                raise RuntimeError(f"run {run_id} is already cancelled")
            cls._processes.setdefault(run_id, {})[process.pid] = process

    @classmethod
    def _unregister(cls, run_id: str, pid: int) -> None:
        with cls._lock:
            processes = cls._processes.get(run_id)
            if not processes:
                return
            processes.pop(pid, None)
            if not processes:
                cls._processes.pop(run_id, None)

    @staticmethod
    def _terminate_tree(pid: int, *, grace_seconds: float) -> None:
        if is_windows():
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                capture_output=True,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            if grace_seconds:
                _wait_for_exit(pid, grace_seconds)
            if _pid_exists(pid):
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    **hidden_subprocess_kwargs(),
                )
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        if grace_seconds:
            _wait_for_exit(pid, grace_seconds)
        if _pid_exists(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @staticmethod
    def _terminate_external_tree(pid: int, *, grace_seconds: float) -> None:
        if is_windows():
            ProcessSupervisor._terminate_tree(pid, grace_seconds=grace_seconds)
            return
        descendants = _descendant_pids(pid)
        for target in [*reversed(descendants), pid]:
            try:
                os.kill(target, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if grace_seconds:
            _wait_for_exit(pid, grace_seconds)
        for target in [*reversed(descendants), pid]:
            if _pid_exists(target):
                try:
                    os.kill(target, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def _bounded(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    suffix = "\n[output truncated by muxdev]"
    kept = encoded[: max(0, limit - len(suffix.encode()))].decode("utf-8", errors="ignore")
    return kept + suffix, True


def _wait_for_exit(pid: int, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and _pid_exists(pid):
        time.sleep(0.05)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _descendant_pids(parent_pid: int) -> list[int]:
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    parents: dict[int, list[int]] = {}
    for child in proc.iterdir():
        if not child.name.isdigit():
            continue
        try:
            fields = (child / "stat").read_text(encoding="utf-8").split()
            pid, parent = int(fields[0]), int(fields[3])
        except (OSError, ValueError, IndexError):
            continue
        parents.setdefault(parent, []).append(pid)
    result: list[int] = []
    stack = list(parents.get(parent_pid, []))
    while stack:
        pid = stack.pop()
        result.append(pid)
        stack.extend(parents.get(pid, []))
    return result
