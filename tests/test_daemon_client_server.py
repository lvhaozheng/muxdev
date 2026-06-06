from __future__ import annotations

import json
import importlib
import shutil
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.api.web import create_app
from muxdev.cli import app
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.process import daemon_status, start_daemon
from muxdev.daemon.tasks import TaskManager
from muxdev.models import ApprovalStatus


runner = CliRunner()


def test_daemon_state_bootstrap_creates_global_store() -> None:
    workspace = _workspace_temp("state")
    try:
        paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
        manager = TaskManager(paths=paths)

        assert paths.config_path.exists()
        assert paths.data_dir.exists()
        assert paths.db_path.exists()
        assert manager.list_tasks() == []
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_daemon_api_task_lifecycle_and_websocket() -> None:
    workspace = _workspace_temp("api")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        client = TestClient(create_app(task_manager=manager))

        assert client.get("/api/health").json()["status"] == "ok"
        submitted = client.post(
            "/api/tasks",
            json={"task": "daemon api smoke", "workspace": str(workspace), "provider": "mock"},
        ).json()
        task_id = submitted["task_id"]
        status = _wait_for_terminal(client, task_id)

        detail = client.get(f"/api/tasks/{task_id}").json()
        diff = client.get(f"/api/tasks/{task_id}/diff").json()
        report = client.get(f"/api/tasks/{task_id}/report").json()
        attach = client.get(f"/api/tasks/{task_id}/attach-command").json()
        rollback = client.post(f"/api/tasks/{task_id}/rollback").json()
        page = client.get("/").text
        with client.websocket_connect("/events") as ws:
            hello = ws.receive_json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert status == "completed"
    assert detail["run"]["run_id"] == task_id
    assert diff["diff"]
    assert "daemon api smoke" in report["content"]
    assert attach["handoff"]["command"]
    assert rollback["status"] in {"rolled_back", "failed"}
    assert "<title>muxdev dashboard</title>" in page
    assert hello["type"] == "hello"


def test_dashboard_provider_health_uses_cache_without_probe(monkeypatch) -> None:
    import muxdev.services.dashboard as dashboard_module

    workspace = _workspace_temp("dashboard-cache")
    cache = workspace / "home" / "cache" / "providers.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            [
                {"provider": "mock", "status": "ready"},
                {"provider": "qwen", "status": "partial"},
            ]
        ),
        encoding="utf-8",
    )

    def fail_probe() -> None:
        raise AssertionError("dashboard refresh must not probe provider CLIs")

    try:
        if hasattr(dashboard_module, "detect_providers"):
            monkeypatch.setattr(dashboard_module, "detect_providers", fail_probe)
        monkeypatch.setattr(dashboard_module, "provider_cache_path", lambda env=None: cache)
        monkeypatch.setattr(
            dashboard_module,
            "load_runtime_config",
            lambda workspace: (_ for _ in ()).throw(AssertionError("cache should be used before config")),
        )

        payload = dashboard_module.startup_dashboard_payload(workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    providers = payload["app"]["providers"]
    assert providers["ready"] == ["mock"]
    assert providers["partial"] == ["qwen"]
    assert providers["total"] == 2
    assert providers["cached"] is True


def test_dashboard_provider_health_falls_back_to_config_without_probe(monkeypatch) -> None:
    import muxdev.services.dashboard as dashboard_module

    workspace = _workspace_temp("dashboard-config")

    def fail_probe() -> None:
        raise AssertionError("dashboard refresh must not probe provider CLIs")

    try:
        if hasattr(dashboard_module, "detect_providers"):
            monkeypatch.setattr(dashboard_module, "detect_providers", fail_probe)
        monkeypatch.setattr(dashboard_module, "provider_cache_path", lambda env=None: workspace / "missing.json")
        monkeypatch.setattr(
            dashboard_module,
            "load_runtime_config",
            lambda workspace: {"cli": {"fallback": ["mock"], "codex": {"command": "codex"}, "mock": {"command": "mock"}}},
        )

        payload = dashboard_module.startup_dashboard_payload(workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    providers = payload["app"]["providers"]
    assert providers["ready"] == ["mock"]
    assert providers["partial"] == []
    assert providers["total"] == 2
    assert providers["cached"] is False


def test_daemon_approvals_and_continue() -> None:
    workspace = _workspace_temp("approval")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        client = TestClient(create_app(task_manager=manager))
        submitted = client.post(
            "/api/tasks",
            json={
                "task": "daemon approval smoke",
                "workspace": str(workspace),
                "provider": "mock",
                "require_approval": ["plan"],
            },
        ).json()
        task_id = submitted["task_id"]
        _wait_for_status(client, task_id, "awaiting_approval")
        approval = client.get("/api/approvals?status=pending").json()[0]

        decided = client.post(f"/api/approvals/{approval['approval_id']}/approve").json()
        continued = client.post(f"/api/tasks/{task_id}/continue").json()
        _wait_for_status(client, task_id, "completed")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert decided["status"] == str(ApprovalStatus.APPROVED)
    assert continued["status"] == "continue_requested"


def test_cli_task_commands_use_daemon_client(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port

        def submit_task(self, payload: dict[str, object]) -> dict[str, object]:
            assert payload["task"] == "cli daemon smoke"
            return {"task_id": "task-1", "run_id": "task-1", "status": "created"}

        def tasks(self) -> list[dict[str, object]]:
            return [{"task_id": "task-1", "task": "cli daemon smoke", "status": "running", "current_stage": "design", "pending_approvals": 0}]

        def task(self, task_id: str) -> dict[str, object]:
            return _detail_payload(task_id)

        def continue_task(self, task_id: str, *, max_cost_usd: float = 0.5) -> dict[str, object]:
            return {"task_id": task_id, "status": "continue_requested", "max_cost_usd": max_cost_usd}

        def stop_task(self, task_id: str) -> dict[str, object]:
            return {"task_id": task_id, "status": "aborted"}

        def approvals(self, *, status: str | None = None) -> list[dict[str, object]]:
            return [{"approval_id": "appr-1", "run_id": "task-1", "stage_id": "design", "type": "plan", "status": status or "pending", "reason": "check"}]

        def approve(self, approval_id: str) -> dict[str, object]:
            return {"approval_id": approval_id, "status": "approved"}

        def deny(self, approval_id: str) -> dict[str, object]:
            return {"approval_id": approval_id, "status": "denied"}

        def diff(self, task_id: str) -> dict[str, object]:
            return {"task_id": task_id, "diff": "diff --git a/x b/x", "path": "diff.patch"}

        def report(self, task_id: str) -> dict[str, object]:
            return {"task_id": task_id, "content": "# report", "path": "final_report.md"}

        def rollback(self, task_id: str) -> dict[str, object]:
            return {"task_id": task_id, "status": "rolled_back"}

        def attach_command(self, task_id: str, *, agent: str = "implementer") -> dict[str, object]:
            return {"task_id": task_id, "agent": agent, "status": "attached", "handoff": {"command": ["tail", "-f", "trace.jsonl"]}}

    cli_main_module = importlib.import_module("muxdev.cli.main")
    monkeypatch.setattr(cli_main_module, "_daemon_client", lambda host="127.0.0.1", port=8788: FakeClient(host=host, port=port))

    run = runner.invoke(app, ["dev", "cli daemon smoke", "--json"])
    tasks = runner.invoke(app, ["tasks", "--json"])
    status = runner.invoke(app, ["status", "task-1", "--json"])
    approve = runner.invoke(app, ["approve", "appr-1", "--json"])

    assert run.exit_code == 0
    assert json.loads(run.stdout)["task_id"] == "task-1"
    assert tasks.exit_code == 0
    assert json.loads(tasks.stdout)[0]["current_stage"] == "design"
    assert status.exit_code == 0
    assert json.loads(status.stdout)["run"]["run_id"] == "task-1"
    assert approve.exit_code == 0
    assert json.loads(approve.stdout)["status"] == "approved"


def test_daemon_status_uses_pid_file_without_repo_state() -> None:
    workspace = _workspace_temp("process")
    try:
        paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
        payload = daemon_status(paths)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["running"] is False
    assert payload["data"].endswith("data")


def test_start_daemon_reuses_healthy_api_without_spawning(monkeypatch) -> None:
    workspace = _workspace_temp("process-healthy")
    try:
        paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
        monkeypatch.setattr("muxdev.daemon.process.daemon_health", lambda **kwargs: {"ok": True, "payload": {"service": "muxdev"}})

        def fail_popen(*args, **kwargs):  # pragma: no cover - should never be reached
            raise AssertionError("start_daemon should not spawn when API is already healthy")

        monkeypatch.setattr("muxdev.daemon.process.subprocess.Popen", fail_popen)
        payload = start_daemon(paths=paths)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["running"] is True
    assert payload["started"] is False


def test_start_daemon_uses_hidden_windows_subprocess_kwargs(monkeypatch) -> None:
    import subprocess

    import muxdev.daemon.process as process_module

    workspace = _workspace_temp("process-hidden")
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 12345
        returncode = None

        def poll(self) -> None:
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    try:
        paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
        monkeypatch.setattr(process_module, "daemon_health", lambda **kwargs: {"ok": False})
        monkeypatch.setattr(process_module, "wait_for_daemon_health", lambda **kwargs: {"ok": True})
        monkeypatch.setattr(process_module.subprocess, "Popen", fake_popen)
        payload = start_daemon(paths=paths)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    kwargs = captured["kwargs"]
    assert payload["started"] is True
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        assert int(kwargs["creationflags"]) & subprocess.CREATE_NO_WINDOW
        assert not (int(kwargs["creationflags"]) & getattr(subprocess, "DETACHED_PROCESS", 0))
        assert kwargs["startupinfo"].wShowWindow == getattr(subprocess, "SW_HIDE", 0)


def test_stop_daemon_can_stop_health_only_daemon_by_port(monkeypatch) -> None:
    import muxdev.daemon.process as process_module

    workspace = _workspace_temp("process-port-stop")
    killed: list[int] = []
    try:
        paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
        paths.pid_path.unlink(missing_ok=True)
        monkeypatch.setattr(process_module, "daemon_health", lambda **kwargs: {"ok": True})
        monkeypatch.setattr(process_module, "_listening_pids_for_ports", lambda ports: [24680])
        monkeypatch.setattr(process_module, "_terminate_pid", lambda pid: killed.append(pid))
        monkeypatch.setattr(process_module, "wait_for_daemon_stop", lambda **kwargs: True)

        payload = process_module.stop_daemon(paths=paths, api_port=8788, ui_port=8787)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["stopped"] is True
    assert payload["pids"] == [24680]
    assert killed == [24680]


def _wait_for_terminal(client: TestClient, task_id: str) -> str:
    for _ in range(80):
        status = client.get(f"/api/tasks/{task_id}").json()["run"]["status"]
        if status in {"completed", "blocked", "awaiting_approval", "paused_budget", "aborted"}:
            return status
        time.sleep(0.1)
    raise AssertionError("task did not reach a terminal or waiting state")


def _wait_for_status(client: TestClient, task_id: str, expected: str) -> None:
    for _ in range(80):
        status = client.get(f"/api/tasks/{task_id}").json()["run"]["status"]
        if status == expected:
            return
        time.sleep(0.1)
    raise AssertionError(f"task did not reach {expected}")


def _detail_payload(task_id: str) -> dict[str, object]:
    return {
        "app": {"workspace": ".", "version": "0.1.0", "providers": {"ready": [], "partial": [], "total": 0}},
        "run": {"run_id": task_id, "task": "cli daemon smoke", "status": "running", "provider": "mock", "workflow": "software-dev", "worktree": "."},
        "stages": [],
        "agents": [],
        "approvals": [],
        "artifacts": [],
        "test_results": [],
        "review_blockers": [],
        "errors": [],
        "usage": [],
        "trace": [],
        "summary": {"tokens": 0, "cost_usd": 0.0},
    }


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
