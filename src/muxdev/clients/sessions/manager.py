"""Persistent session registry used by CLI, TUI, and provider backends."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ...core.platforms import hidden_subprocess_kwargs, is_windows
from ...config.loader import path_config


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    provider: str
    command: list[str]
    cwd: str
    pid: int | None
    status: str
    transcript: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["alive"] = self.pid is not None and is_pid_alive(self.pid)
        return data


class SessionManager:
    def __init__(self, workspace: Path):
        self.root = path_config(workspace, "sessions")
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "sessions.json"

    def list(self) -> list[SessionRecord]:
        if not self.index_path.exists():
            return []
        return [SessionRecord(**item) for item in json.loads(self.index_path.read_text(encoding="utf-8"))]

    def start(self, provider: str, command: list[str], *, cwd: Path) -> SessionRecord:
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        transcript = self.root / f"{session_id}.log"
        transcript_handle = transcript.open("ab")
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=transcript_handle,
                stderr=subprocess.STDOUT,
                close_fds=True,
                **hidden_subprocess_kwargs(new_process_group=True),
            )
        finally:
            transcript_handle.close()
        record = SessionRecord(
            session_id=session_id,
            provider=provider,
            command=command,
            cwd=str(cwd),
            pid=process.pid,
            status="running",
            transcript=str(transcript),
        )
        self._upsert(record)
        return record

    def stop(self, session_id: str) -> SessionRecord:
        record = self.get(session_id)
        if record.pid and is_pid_alive(record.pid):
            try:
                if is_windows():
                    subprocess.run(
                        ["taskkill", "/PID", str(record.pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        check=False,
                        **hidden_subprocess_kwargs(),
                    )
                else:
                    os.kill(record.pid, signal.SIGTERM)
            except Exception:
                try:
                    os.kill(record.pid, signal.SIGTERM)
                except Exception:
                    pass
        updated = SessionRecord(
            session_id=record.session_id,
            provider=record.provider,
            command=record.command,
            cwd=record.cwd,
            pid=record.pid,
            status="stopped",
            transcript=record.transcript,
        )
        self._upsert(updated)
        return updated

    def get(self, session_id: str) -> SessionRecord:
        for record in self.list():
            if record.session_id == session_id:
                return record
        raise ValueError(f"session not found: {session_id}")

    def _upsert(self, record: SessionRecord) -> None:
        rows = [item for item in self.list() if item.session_id != record.session_id]
        rows.append(record)
        self.index_path.write_text(json.dumps([asdict(row) for row in rows], indent=2), encoding="utf-8")


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
