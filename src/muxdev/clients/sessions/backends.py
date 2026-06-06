"""Session backends for provider execution and attach workflows.

HeadlessSubprocessBackend is used for deterministic CLI execution and transcript
capture. PtyBackend and TmuxBackend model longer-lived interactive workflows
without forcing every platform to provide native terminal takeover.
"""

from __future__ import annotations

import json
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from time import time

from ...core.platforms import hidden_subprocess_kwargs, shell_join
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
    ) -> SessionResult:
        """Execute a command, parse stream events, and enforce an idle timeout."""
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                **hidden_subprocess_kwargs(),
            )
            events: list[StreamEvent] = []
            stdout_parts: list[str] = []
            start = time()
            transcript_handle = transcript_path.open("a", encoding="utf-8") if transcript_path else None
            chunks_handle = chunks_path.open("a", encoding="utf-8") if chunks_path else None
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    if time() - start > timeout:
                        process.kill()
                        events.append(self.adapter.idle_timeout(timeout))
                        returncode = 124
                        break
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
                else:
                    returncode = process.wait(timeout=max(0.1, timeout - (time() - start)))
            finally:
                if transcript_handle:
                    transcript_handle.close()
                if chunks_handle:
                    chunks_handle.close()
            events.append(self.adapter.cli_exited(returncode))
            return SessionResult(returncode, "".join(stdout_parts), "", events)
        except subprocess.TimeoutExpired:
            events = self.adapter.parse_chunk("")
            events.append(self.adapter.idle_timeout(timeout))
            return SessionResult(124, "", "", events)


class PtyBackend(HeadlessSubprocessBackend):
    """Subprocess-backed PTY-like backend for attach/detach session bookkeeping."""

    def attach(self, session_id: str) -> str:
        return f"attach requested for {session_id}; native PTY attach is not interactive in M2"

    def detach(self, session_id: str) -> str:
        return f"detach requested for {session_id}"


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


def _chunks(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    return lines or ([text] if text else [])
