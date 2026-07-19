"""Filesystem locations for the muxdev daemon."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..core.private_paths import muxdev_private_home


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
                        "[daemon]",
                        "workers = 2",
                        "lease_seconds = 30",
                        "heartbeat_seconds = 5",
                        "cancel_grace_seconds = 10",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        return self


def default_daemon_paths(env: dict[str, str] | None = None) -> DaemonPaths:
    env = os.environ if env is None else env
    home = muxdev_private_home(env)
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


def daemon_runtime_settings(paths: DaemonPaths) -> dict[str, int]:
    defaults = {
        "workers": 2,
        "lease_ms": 30_000,
        "heartbeat_ms": 5_000,
        "cancel_grace_ms": 10_000,
    }
    try:
        payload = tomllib.loads(paths.config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return defaults
    daemon = payload.get("daemon", {}) if isinstance(payload, dict) else {}
    if not isinstance(daemon, dict):
        return defaults
    return {
        "workers": max(1, int(daemon.get("workers", defaults["workers"]))),
        "lease_ms": max(1_000, int(float(daemon.get("lease_seconds", 30)) * 1_000)),
        "heartbeat_ms": max(100, int(float(daemon.get("heartbeat_seconds", 5)) * 1_000)),
        "cancel_grace_ms": max(100, int(float(daemon.get("cancel_grace_seconds", 10)) * 1_000)),
    }
