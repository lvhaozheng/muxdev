"""Filesystem locations for the muxdev daemon."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 8787
DEFAULT_API_PORT = 8788


@dataclass(frozen=True)
class DaemonPaths:
    home: Path
    config_path: Path
    data_dir: Path
    db_path: Path
    runs_dir: Path
    worktrees_dir: Path
    logs_dir: Path
    log_path: Path
    pid_path: Path

    def ensure(self) -> "DaemonPaths":
        self.home.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(
                "\n".join(
                    [
                        f'host = "{DEFAULT_HOST}"',
                        f"dashboard_port = {DEFAULT_UI_PORT}",
                        f"api_port = {DEFAULT_API_PORT}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        return self


def default_daemon_paths(env: dict[str, str] | None = None) -> DaemonPaths:
    env = os.environ if env is None else env
    home = Path(env.get("MUXDEV_HOME") or Path.home() / ".muxdev").expanduser()
    data = home / "data"
    return DaemonPaths(
        home=home,
        config_path=home / "config.toml",
        data_dir=data,
        db_path=data / "muxdev.sqlite",
        runs_dir=data / "runs",
        worktrees_dir=data / "worktrees",
        logs_dir=data / "logs",
        log_path=data / "logs" / "daemon.log",
        pid_path=data / "muxdev.pid",
    )
