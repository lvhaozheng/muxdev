"""Session backends for provider execution and attach workflows.

HeadlessSubprocessBackend is used for deterministic CLI execution and transcript
capture. PtyBackend and TmuxBackend model longer-lived interactive workflows
without forcing every platform to provide native terminal takeover.
"""

from __future__ import annotations

import json
import locale
import os
import queue
import subprocess
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from time import time

from ...core.platforms import hidden_subprocess_kwargs, is_windows, shell_join
from ..stream import StreamAdapter, StreamEvent


@dataclass(frozen=True)
class SessionResult:
    """Normalized subprocess/session result."""

    returncode: int
    stdout: str
    stderr: str
    events: list[StreamEvent]


class HeadlessSubprocessBackend:
    """Run a command while streaming chunks into transcript artifacts."""

    def __init__(self, adapter: StreamAdapter | None = None):
        self.adapter = adapter or StreamAdapter()

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout: float = 30,
        transcript_path: Path | None = None,
        chunks_path: Path | None = None,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SessionResult:
        """Execute a command, parse stream events, and enforce an idle timeout."""
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=_provider_subprocess_env(env),
                **hidden_subprocess_kwargs(),
            )
            events: list[StreamEvent] = []
            stdout_parts: list[str] = []
            last_activity = time()
            transcript_handle = transcript_path.open("a", encoding="utf-8") if transcript_path else None
            chunks_handle = chunks_path.open("a", encoding="utf-8") if chunks_path else None
            lines: queue.Queue[str] = queue.Queue()
            returncode: int | None = None

            def read_stdout() -> None:
                assert process.stdout is not None
                for stdout_line in process.stdout:
                    lines.put(_decode_process_output(stdout_line))

            def write_stdin() -> None:
                if input_text is None or process.stdin is None:
                    return
                try:
                    process.stdin.write(input_text.encode("utf-8"))
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

            reader = threading.Thread(target=read_stdout, name="muxdev-provider-stdout", daemon=True)
            reader.start()
            input_writer = threading.Thread(target=write_stdin, name="muxdev-provider-stdin", daemon=True)
            input_writer.start()
            try:
                while True:
                    if process.poll() is not None and lines.empty() and not reader.is_alive():
                        break
                    try:
                        line = lines.get(timeout=0.1)
                    except queue.Empty:
                        if time() - last_activity > timeout:
                            _terminate_process_tree(process)
                            process.wait(timeout=3)
                            events.append(self.adapter.idle_timeout(timeout))
                            returncode = 124
                            break
                        continue
                    last_activity = time()
                    stdout_parts.append(line)
                    if transcript_handle:
                        transcript_handle.write(line)
                        transcript_handle.flush()
                    parsed = self.adapter.parse_chunk(line)
                    events.extend(parsed)
                    if chunks_handle:
                        chunks_handle.write(
                            json.dumps(
                                {
                                    "time": time(),
                                    "chunk": line,
                                    "events": [{"type": str(event.type), "text": event.text} for event in parsed],
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        chunks_handle.flush()
                if returncode is None:
                    returncode = process.wait(timeout=3)
            finally:
                reader.join(timeout=1)
                input_writer.join(timeout=1)
                if transcript_handle:
                    transcript_handle.close()
                if chunks_handle:
                    chunks_handle.close()
            events.append(self.adapter.cli_exited(returncode))
            return SessionResult(returncode, "".join(stdout_parts), "", events)
        except subprocess.TimeoutExpired:
            try:
                _terminate_process_tree(process)
            except UnboundLocalError:
                pass
            events = self.adapter.parse_chunk("")
            events.append(self.adapter.idle_timeout(timeout))
            return SessionResult(124, "", "", events)


class PtyBackend(HeadlessSubprocessBackend):
    """Subprocess-backed PTY-like backend for attach/detach session bookkeeping."""

    def attach(self, session_id: str) -> str:
        return f"attach requested for {session_id}; native PTY attach is not interactive in M2"

    def detach(self, session_id: str) -> str:
        return f"detach requested for {session_id}"


class ConptyBackend(PtyBackend):
    """Windows ConPTY-capable session placeholder with subprocess fallback."""

    @property
    def available(self) -> bool:
        return is_windows()

    def attach(self, session_id: str) -> str:
        if not self.available:
            return f"conpty is only available on Windows; attach requested for {session_id}"
        return f"conpty attach requested for {session_id}; interactive takeover is handled by provider CLI"


class TmuxBackend:
    """Small wrapper around tmux for POSIX attach-capable sessions."""

    def __init__(self, tmux: str | None = None):
        self.tmux = shutil.which("tmux") if tmux is None else tmux

    @property
    def available(self) -> bool:
        return bool(self.tmux)

    def start(self, session_id: str, command: list[str], *, cwd: Path) -> SessionResult:
        if not self.tmux:
            return SessionResult(127, "", "tmux command not found", [])
        tmux_command = [
            self.tmux,
            "new-session",
            "-d",
            "-s",
            session_id,
            "-c",
            str(cwd),
            shell_join(command),
        ]
        completed = subprocess.run(
            tmux_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **hidden_subprocess_kwargs(),
        )
        return SessionResult(completed.returncode, completed.stdout or "", completed.stderr or "", [])

    def attach_command(self, session_id: str) -> list[str]:
        if not self.tmux:
            raise RuntimeError("tmux command not found")
        return [self.tmux, "attach-session", "-t", session_id]

    def stop(self, session_id: str) -> SessionResult:
        if not self.tmux:
            return SessionResult(127, "", "tmux command not found", [])
        completed = subprocess.run(
            [self.tmux, "kill-session", "-t", session_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **hidden_subprocess_kwargs(),
        )
        return SessionResult(completed.returncode, completed.stdout or "", completed.stderr or "", [])


class DockerBackend:
    """Run a provider command inside a Docker workdir mount when Docker exists."""

    def __init__(self, docker: str | None = None, adapter: StreamAdapter | None = None):
        self.docker = shutil.which("docker") if docker is None else docker
        self.headless = HeadlessSubprocessBackend(adapter=adapter)

    @property
    def available(self) -> bool:
        return bool(self.docker)

    def run(
        self,
        command: list[str],
        *,
        cwd: Path,
        image: str = "python:3.11-slim",
        timeout: float = 30,
        transcript_path: Path | None = None,
        chunks_path: Path | None = None,
    ) -> SessionResult:
        if not self.docker:
            return SessionResult(127, "", "docker command not found", [])
        docker_command = [
            self.docker,
            "run",
            "--rm",
            "-v",
            f"{cwd}:/workspace",
            "-w",
            "/workspace",
            image,
            *command,
        ]
        return self.headless.run(
            docker_command,
            cwd=cwd,
            timeout=timeout,
            transcript_path=transcript_path,
            chunks_path=chunks_path,
        )


def _chunks(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    return lines or ([text] if text else [])


def _terminate_process_tree(process: subprocess.Popen[object]) -> None:
    if process.poll() is not None:
        return
    if is_windows():
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        return
    process.kill()


def _decode_process_output(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    preferred = locale.getpreferredencoding(False)
    encodings = ["utf-8", "utf-8-sig"]
    if preferred:
        encodings.append(preferred)
    encodings.extend(["mbcs", "gbk", "cp936"])
    seen: set[str] = set()
    for encoding in encodings:
        if encoding in seen:
            continue
        seen.add(encoding)
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _provider_subprocess_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Keep provider-spawned shells from creating noisy transient Python files."""
    env = os.environ.copy()
    env.update(overrides or {})
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    pytest_addopts = env.get("PYTEST_ADDOPTS", "")
    cache_flag = "-p no:cacheprovider"
    if cache_flag not in pytest_addopts:
        env["PYTEST_ADDOPTS"] = f"{pytest_addopts} {cache_flag}".strip()
    warning_filter = "ignore:urllib3.*:Warning:requests"
    python_warnings = env.get("PYTHONWARNINGS", "")
    filters = [item.strip() for item in python_warnings.split(",") if item.strip()]
    if warning_filter not in filters:
        filters.append(warning_filter)
        env["PYTHONWARNINGS"] = ",".join(filters)
    return env
