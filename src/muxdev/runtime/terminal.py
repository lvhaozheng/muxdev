"""Cross-platform terminal backends used by persistent coding CLI sessions."""

from __future__ import annotations

import os
import queue
import select
import shutil
import signal
import struct
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ..core.platforms import hidden_subprocess_kwargs


@dataclass(frozen=True)
class TerminalSnapshot:
    backend: str
    running: bool
    exit_code: int | None
    cols: int
    rows: int
    resumable: bool


class TerminalBackend(ABC):
    """A live terminal process. All implementations accept argv, never shell text."""

    backend_name = "abstract"
    supports_resize = False
    resumable = False

    def __init__(self) -> None:
        self.cols = 120
        self.rows = 32

    @abstractmethod
    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        cols: int = 120,
        rows: int = 32,
    ) -> None: ...

    @abstractmethod
    def write(self, data: str) -> None: ...

    @abstractmethod
    def read(self, *, timeout: float = 0.25) -> str: ...

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = _dimensions(cols, rows)

    def attach(self) -> TerminalSnapshot:
        return self.snapshot()

    def interrupt(self) -> None:
        """Interrupt the foreground command without closing the terminal session."""
        self.write("\x03")

    @abstractmethod
    def snapshot(self) -> TerminalSnapshot: ...

    @abstractmethod
    def close(self) -> None: ...


class PipeTerminalBackend(TerminalBackend):
    """Portable headless fallback when a real PTY is unavailable."""

    backend_name = "pipe"

    def __init__(self) -> None:
        super().__init__()
        self.process: subprocess.Popen[str] | None = None
        self.output: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        self.cols, self.rows = _dimensions(cols, rows)
        self.process = subprocess.Popen(
            list(argv), cwd=cwd, env=dict(env), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            **hidden_subprocess_kwargs(),
        )
        self._reader = threading.Thread(target=self._read_pipe, daemon=True)
        self._reader.start()

    def _read_pipe(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for data in iter(lambda: self.process.stdout.read(4096), ""):
                if data:
                    self.output.put(data)
        finally:
            self.output.put(None)

    def write(self, data: str) -> None:
        if not self.process or not self.process.stdin or self.process.poll() is not None:
            raise RuntimeError("terminal process is not running")
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def read(self, *, timeout: float = 0.25) -> str:
        try:
            item = self.output.get(timeout=max(0.01, timeout))
        except queue.Empty:
            return ""
        return item or ""

    def snapshot(self) -> TerminalSnapshot:
        code = self.process.poll() if self.process else None
        return TerminalSnapshot(self.backend_name, self.process is not None and code is None, code, self.cols, self.rows, False)

    def interrupt(self) -> None:
        if not self.process or self.process.poll() is not None:
            raise RuntimeError("terminal process is not running")
        try:
            if os.name == "nt":
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.process.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            super().interrupt()

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()


class PosixPtyBackend(TerminalBackend):
    backend_name = "posix-pty"
    supports_resize = True

    def __init__(self) -> None:
        super().__init__()
        self.process: subprocess.Popen[bytes] | None = None
        self.master_fd: int | None = None

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        import pty

        self.cols, self.rows = _dimensions(cols, rows)
        master, slave = pty.openpty()
        self.master_fd = master
        self._set_size()
        self.process = subprocess.Popen(
            list(argv), cwd=cwd, env=dict(env), stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True, close_fds=True,
        )
        os.close(slave)
        os.set_blocking(master, False)

    def write(self, data: str) -> None:
        if self.master_fd is None or not self.process or self.process.poll() is not None:
            raise RuntimeError("terminal process is not running")
        os.write(self.master_fd, data.encode("utf-8"))

    def read(self, *, timeout: float = 0.25) -> str:
        if self.master_fd is None:
            return ""
        ready, _, _ = select.select([self.master_fd], [], [], max(0.0, timeout))
        if not ready:
            return ""
        try:
            return os.read(self.master_fd, 65536).decode("utf-8", errors="replace")
        except (BlockingIOError, OSError):
            return ""

    def resize(self, cols: int, rows: int) -> None:
        super().resize(cols, rows)
        self._set_size()

    def _set_size(self) -> None:
        if self.master_fd is None:
            return
        import fcntl
        import termios

        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))

    def snapshot(self) -> TerminalSnapshot:
        code = self.process.poll() if self.process else None
        return TerminalSnapshot(self.backend_name, self.process is not None and code is None, code, self.cols, self.rows, self.resumable)

    def interrupt(self) -> None:
        if not self.process or self.process.poll() is not None:
            raise RuntimeError("terminal process is not running")
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            super().interrupt()

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None


class ConPtyBackend(TerminalBackend):
    backend_name = "conpty"
    supports_resize = True

    def __init__(self) -> None:
        super().__init__()
        self.process: object | None = None

    @staticmethod
    def available() -> bool:
        try:
            from winpty import PtyProcess  # noqa: F401
        except ImportError:
            return False
        return True

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        from winpty import PtyProcess

        self.cols, self.rows = _dimensions(cols, rows)
        try:
            self.process = PtyProcess.spawn(
                list(argv), cwd=str(cwd), env=dict(env), dimensions=(self.rows, self.cols)
            )
        except TypeError:
            self.process = PtyProcess.spawn(
                subprocess.list2cmdline(list(argv)), cwd=str(cwd), env=dict(env),
                dimensions=(self.rows, self.cols),
            )

    def write(self, data: str) -> None:
        if self.process is None or not self._alive():
            raise RuntimeError("terminal process is not running")
        self.process.write(data)  # type: ignore[attr-defined]

    def read(self, *, timeout: float = 0.25) -> str:
        if self.process is None or not self._alive():
            return ""
        try:
            return str(self.process.read(65536, blocking=False))  # type: ignore[attr-defined]
        except TypeError:
            try:
                return str(self.process.read(65536))  # type: ignore[attr-defined]
            except EOFError:
                return ""
        except (EOFError, OSError):
            return ""

    def resize(self, cols: int, rows: int) -> None:
        super().resize(cols, rows)
        if self.process is not None:
            self.process.setwinsize(self.rows, self.cols)  # type: ignore[attr-defined]

    def _alive(self) -> bool:
        return bool(self.process and self.process.isalive())  # type: ignore[attr-defined]

    def snapshot(self) -> TerminalSnapshot:
        alive = self._alive()
        code = None if alive or self.process is None else int(self.process.exitstatus)  # type: ignore[attr-defined]
        return TerminalSnapshot(self.backend_name, alive, code, self.cols, self.rows, False)

    def interrupt(self) -> None:
        if self.process is None or not self._alive():
            raise RuntimeError("terminal process is not running")
        self.process.write("\x03")  # type: ignore[attr-defined]

    def close(self) -> None:
        if self.process is not None and self._alive():
            self.process.terminate(force=False)  # type: ignore[attr-defined]


class TmuxTerminalBackend(PosixPtyBackend):
    backend_name = "tmux"
    resumable = True

    def __init__(self, session_name: str) -> None:
        super().__init__()
        self.session_name = session_name

    def spawn(self, argv: Sequence[str], **kwargs: object) -> None:
        command = ["tmux", "new-session", "-A", "-s", self.session_name, "--", *argv]
        super().spawn(command, **kwargs)  # type: ignore[arg-type]


def terminal_backend(
    *,
    supports_pty: bool,
    prefer_tmux: bool = False,
    tmux_session_name: str | None = None,
) -> TerminalBackend:
    if not supports_pty:
        return PipeTerminalBackend()
    if os.name == "nt":
        return ConPtyBackend() if ConPtyBackend.available() else PipeTerminalBackend()
    if prefer_tmux and tmux_session_name and shutil.which("tmux"):
        return TmuxTerminalBackend(tmux_session_name)
    return PosixPtyBackend()


def terminal_capabilities(*, supports_pty: bool, prefer_tmux: bool = False) -> dict[str, object]:
    """Report the backend this host can actually provide, not adapter wishes."""
    backend = terminal_backend(
        supports_pty=supports_pty,
        prefer_tmux=prefer_tmux,
        tmux_session_name="muxdev-doctor",
    )
    real_pty = backend.backend_name != "pipe"
    return {
        "backend": backend.backend_name,
        "pty": real_pty,
        "resize": bool(real_pty and backend.supports_resize),
        "process_resume": bool(real_pty and backend.resumable),
        "degraded": bool(supports_pty and not real_pty),
        "degradation_reason": (
            "ConPTY/PTY backend is unavailable; interactive resize and resume are disabled"
            if supports_pty and not real_pty
            else None
        ),
    }


def _dimensions(cols: int, rows: int) -> tuple[int, int]:
    return max(20, min(int(cols), 500)), max(5, min(int(rows), 200))


__all__ = [
    "ConPtyBackend", "PipeTerminalBackend", "PosixPtyBackend", "TerminalBackend",
    "TerminalSnapshot", "TmuxTerminalBackend", "terminal_backend", "terminal_capabilities",
]
