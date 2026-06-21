"""Cross-platform PID-file process management for the muxdev daemon."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..core.platforms import hidden_subprocess_kwargs, is_windows
from .paths import DEFAULT_API_PORT, DEFAULT_HOST, DEFAULT_UI_PORT, DaemonPaths, default_daemon_paths


def daemon_status(paths: DaemonPaths | None = None) -> dict[str, Any]:
    paths = (paths or default_daemon_paths()).ensure()
    pid = read_pid(paths.pid_path)
    alive = pid is not None and is_pid_alive(pid)
    if pid is not None and not alive:
        _unlink_pid_file(paths.pid_path)
    return {
        "running": alive,
        "pid": pid if alive else None,
        "pid_file": str(paths.pid_path),
        "log": str(paths.log_path),
        "data": str(paths.data_dir),
        "config": str(paths.config_path),
    }


def start_daemon(
    *,
    host: str = DEFAULT_HOST,
    api_port: int = DEFAULT_API_PORT,
    ui_port: int = DEFAULT_UI_PORT,
    paths: DaemonPaths | None = None,
) -> dict[str, Any]:
    paths = (paths or default_daemon_paths()).ensure()
    current = daemon_status(paths)
    if current["running"]:
        return {**current, "started": False, "dashboard_url": f"http://{host}:{ui_port}", "api_url": f"http://{host}:{api_port}"}
    health = daemon_health(host=host, api_port=api_port)
    if health.get("ok"):
        return {
            **current,
            "running": True,
            "started": False,
            "pid": current.get("pid"),
            "dashboard_url": f"http://{host}:{ui_port}",
            "api_url": f"http://{host}:{api_port}",
            "health": health,
        }
    occupied_pids = _listening_pids_for_ports({api_port, ui_port})
    if occupied_pids:
        return {
            **current,
            "running": False,
            "started": False,
            "dashboard_url": f"http://{host}:{ui_port}",
            "api_url": f"http://{host}:{api_port}",
            "health": health,
            "error": "port_conflict",
            "message": f"ports {api_port}/{ui_port} are already in use; stop the owning process or choose different ports",
            "pids": occupied_pids,
        }

    command = [
        sys.executable,
        "-m",
        "muxdev.daemon.server",
        "--host",
        host,
        "--api-port",
        str(api_port),
        "--ui-port",
        str(ui_port),
    ]
    with paths.log_path.open("ab") as log:
        log.write(f"\n--- muxdev daemon start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode("utf-8"))
        process = subprocess.Popen(
            command,
            cwd=Path.cwd(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **hidden_subprocess_kwargs(background=True),
        )
    paths.pid_path.write_text(str(process.pid), encoding="utf-8")
    health = wait_for_daemon_health(host=host, api_port=api_port, process=process)
    running = process.poll() is None
    if not running:
        _unlink_pid_file(paths.pid_path)
    return {
        "running": running,
        "started": running,
        "pid": process.pid,
        "pid_file": str(paths.pid_path),
        "log": str(paths.log_path),
        "data": str(paths.data_dir),
        "config": str(paths.config_path),
        "dashboard_url": f"http://{host}:{ui_port}",
        "api_url": f"http://{host}:{api_port}",
        "health": health,
    }


def stop_daemon(
    paths: DaemonPaths | None = None,
    *,
    host: str = DEFAULT_HOST,
    api_port: int = DEFAULT_API_PORT,
    ui_port: int = DEFAULT_UI_PORT,
) -> dict[str, Any]:
    paths = (paths or default_daemon_paths()).ensure()
    pid = read_pid(paths.pid_path)
    port_pids = _listening_pids_for_ports({api_port, ui_port})
    attempted_pids: list[int] = []
    if pid is not None and is_pid_alive(pid):
        _terminate_pid(pid)
        attempted_pids.append(pid)
    else:
        _unlink_pid_file(paths.pid_path)
    for owner in port_pids:
        if owner in attempted_pids:
            continue
        _terminate_pid(owner)
        attempted_pids.append(owner)
    if attempted_pids:
        wait_for_daemon_stop(host=host, api_port=api_port)
        _wait_for_pids_exit(attempted_pids)

    remaining_port_pids = _listening_pids_for_ports({api_port, ui_port})
    remaining_pid = pid if pid is not None and is_pid_alive(pid) else None
    remaining_pids = sorted(set(remaining_port_pids + ([remaining_pid] if remaining_pid is not None else [])))
    running = bool(remaining_pids)
    if not running or remaining_pid is None:
        _unlink_pid_file(paths.pid_path)
    return {
        "running": running,
        "stopped": bool(attempted_pids) and not running,
        "pid": remaining_pid,
        "pids": attempted_pids,
        "port_pids": port_pids,
        "remaining_pids": remaining_pids,
        "remaining_port_pids": remaining_port_pids,
    }


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _unlink_pid_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def daemon_health(*, host: str = DEFAULT_HOST, api_port: int = DEFAULT_API_PORT, timeout: float = 2.0) -> dict[str, Any]:
    url = f"http://{host}:{api_port}/api/health"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return {"ok": False, "url": url, "error": str(exc)}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    ok = payload.get("service") == "muxdev" and payload.get("status") == "ok"
    return {"ok": ok, "url": url, "payload": payload}


def wait_for_daemon_health(
    *,
    host: str,
    api_port: int,
    process: subprocess.Popen[Any],
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {"ok": False, "url": f"http://{host}:{api_port}/api/health", "error": "not checked"}
    while time.time() < deadline:
        if process.poll() is not None:
            return {**last, "ok": False, "error": f"daemon exited with code {process.returncode}"}
        last = daemon_health(host=host, api_port=api_port)
        if last.get("ok"):
            return last
        time.sleep(0.15)
    return last


def wait_for_daemon_stop(*, host: str, api_port: int, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not daemon_health(host=host, api_port=api_port).get("ok"):
            return True
        time.sleep(0.15)
    return False


def _wait_for_pids_exit(pids: list[int], timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    unique_pids = sorted(set(pids))
    while time.time() < deadline:
        if all(not is_pid_alive(pid) for pid in unique_pids):
            return True
        time.sleep(0.15)
    return all(not is_pid_alive(pid) for pid in unique_pids)


def _terminate_pid(pid: int) -> None:
    try:
        if is_windows():
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def _listening_pids_for_ports(ports: set[int]) -> list[int]:
    if not ports or not is_windows():
        return []
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []

    pids: set[int] = set()
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
            continue
        try:
            port = int(parts[1].rsplit(":", 1)[1])
            pid = int(parts[4])
        except (IndexError, ValueError):
            continue
        if port in ports:
            pids.add(pid)
    return sorted(pids)
