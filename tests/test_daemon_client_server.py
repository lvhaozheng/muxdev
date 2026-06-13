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
from muxdev.models import ApprovalStatus, ProviderActionStatus


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
        project_run_dir = workspace / ".muxdev" / "runs" / task_id
        project_task_context_exists = (project_run_dir / "task_context.json").exists()
        legacy_global_run_exists = (manager.paths.runs_dir / task_id).exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert status == "completed"
    assert project_task_context_exists
    assert not legacy_global_run_exists
    assert detail["run"]["run_id"] == task_id
    assert detail["run"]["worktree"].startswith(str(workspace / ".muxdev"))
    assert diff["diff"]
    assert diff["path"].startswith(str(project_run_dir))
    assert "daemon api smoke" in report["content"]
    assert report["path"].startswith(str(project_run_dir))
    assert attach["handoff"]["command"]
    assert rollback["status"] in {"rolled_back", "failed"}
    assert "<title>muxdev Mission Control</title>" in page
    assert hello["type"] == "hello"


def test_daemon_legacy_global_run_remains_readable() -> None:
    workspace = _workspace_temp("legacy-global")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        run_dir = manager.paths.runs_dir / "run_legacy_global"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "task_context.json").write_text(json.dumps({"profile": "legacy"}), encoding="utf-8")
        (run_dir / "diff.patch").write_text("legacy diff", encoding="utf-8")
        (run_dir / "final_report.md").write_text("legacy report", encoding="utf-8")
        with manager.board() as board:
            board.create_run(
                run_id="run_legacy_global",
                task="legacy global run",
                workflow="software-dev",
                provider="mock",
                workspace=workspace,
                worktree=run_dir / "worktree",
            )

        detail = manager.task_detail("run_legacy_global")
        diff = manager.diff("run_legacy_global")
        report = manager.report("run_legacy_global")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert detail["run"]["run_id"] == "run_legacy_global"
    assert diff["path"] == str(run_dir / "diff.patch")
    assert diff["diff"] == "legacy diff"
    assert report["path"] == str(run_dir / "final_report.md")
    assert report["content"] == "legacy report"


def test_live_dashboard_renders_mission_control_sections() -> None:
    from muxdev.api.web import render_live_dashboard_html

    html = render_live_dashboard_html("run_demo")

    assert "muxdev Mission Control" in html
    assert "Projects" in html
    assert "Global Config" in html
    assert "Workflows" in html
    assert "Tasks" in html
    assert "Activity" in html
    assert "Artifacts" in html
    assert "Config" in html
    assert "Current Status" in html
    assert "Action Center" in html
    assert "MCP" in html
    assert "local stdio" in html
    assert "tools" in html
    assert "guarded writes" in html
    assert "tab-mcp" not in html
    assert "Task Board" in html
    assert "Task Timeline" in html
    assert "Provider Action Wizard" in html
    assert "Approval Risk Review" in html
    assert "Evidence / Artifacts Center" in html
    assert "project-name" in html
    assert "project-path" in html
    assert "project-hide" in html
    assert "hover-detail" in html
    assert "/dashboard/overview" in html
    assert "Copy attach command" in html
    assert "Mark handled and continue" in html


def test_dashboard_overview_groups_projects_workflows_roles(monkeypatch) -> None:
    workspace = _workspace_temp("dashboard-overview")
    try:
        import muxdev.api.web as web_module

        project = workspace / "project-a"
        project.mkdir(parents=True)
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        for run_id, task in (("run_dash_1", "ship dashboard"), ("run_dash_2", "polish dashboard")):
            run_dir = manager.paths.runs_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "task_context.json").write_text(
                json.dumps({"profile": "squad", "gate": "safe", "role_providers": {"code": "mock"}, "skills": [{"name": "docs-update", "role": "docs"}]}),
                encoding="utf-8",
            )
            with manager.board() as board:
                board.create_run(run_id=run_id, task=task, workflow="dev", provider="mock", workspace=project, worktree=project / ".muxdev" / run_id)
                board.set_run_status(run_id, "running")
                board.upsert_stage(run_id, "code", role="code", status="running", summary="coding")

        monkeypatch.setattr(web_module, "_provider_health_payload", lambda: {"ready": ["mock"], "partial": [], "unavailable": [], "total": 1, "providers": []})
        client = TestClient(create_app(task_manager=manager))
        payload = client.get("/api/dashboard/overview", params={"workspace": str(project)}).json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["selected_project_id"] == payload["projects"][0]["id"]
    assert payload["projects"][0]["path"] == str(project.resolve())
    workflow = payload["projects"][0]["workflows"][0]
    assert workflow["id"] == "dev"
    code_group = next(group for group in workflow["role_groups"] if group["role"] == "code")
    assert {task["task_id"] for task in code_group["tasks"]} == {"run_dash_1", "run_dash_2"}
    first = next(task for task in code_group["tasks"] if task["task_id"] == "run_dash_1")
    assert first["diff_endpoint"] == "/api/tasks/run_dash_1/diff"
    assert payload["global_config"]["role_templates"]
    assert "plugin_market" not in payload["global_config"]
    assert payload["global_config"]["workflow_templates"]["templates"]
    assert "catalog" in payload["global_config"]["skills_catalog"]
    mcp = payload["global_config"]["mcp"]
    assert mcp["status"] == "enabled"
    assert mcp["mode"] == "local stdio"
    assert mcp["tools_count"] > 0
    assert mcp["resources_count"] > 0
    assert mcp["prompts_count"] > 0
    assert mcp["write_policy"] == "guarded"
    assert len(mcp["recent_guardrails"]) <= 3


def test_dashboard_project_hide_and_restore(monkeypatch) -> None:
    workspace = _workspace_temp("dashboard-hide")
    try:
        import muxdev.api.web as web_module

        project_a = workspace / "project-a"
        project_b = workspace / "project-b"
        project_a.mkdir(parents=True)
        project_b.mkdir(parents=True)
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        with manager.board() as board:
            board.create_run(run_id="run_project_a", task="project a", workflow="dev", provider="mock", workspace=project_a, worktree=project_a / ".muxdev" / "run")
            board.create_run(run_id="run_project_b", task="project b", workflow="dev", provider="mock", workspace=project_b, worktree=project_b / ".muxdev" / "run")
            board.create_approval("run_project_a", "code", "plan", "approve project a")
            board.create_provider_action(
                run_id="run_project_a",
                stage_id="code",
                provider="mock",
                role="code",
                kind="cli_confirmation",
                prompt_text="Continue project a?",
                options=[],
            )

        monkeypatch.setattr(web_module, "_provider_health_payload", lambda: {"ready": ["mock"], "partial": [], "unavailable": [], "total": 1, "providers": []})
        client = TestClient(create_app(task_manager=manager))
        before = client.get("/api/dashboard/overview", params={"workspace": str(project_a)}).json()
        project_id = next(project["id"] for project in before["projects"] if project["path"] == str(project_a.resolve()))
        assert before["pending_approvals"]
        assert before["pending_provider_actions"]
        hidden = client.delete(f"/api/dashboard/projects/{project_id}").json()
        after = client.get("/api/dashboard/overview", params={"workspace": str(project_a)}).json()
        with_hidden = client.get("/api/dashboard/overview", params={"workspace": str(project_a), "include_hidden": "true"}).json()
        restored = client.post(f"/api/dashboard/projects/{project_id}/restore").json()
        final = client.get("/api/dashboard/overview", params={"workspace": str(project_a)}).json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert hidden["hidden"] is True
    assert project_id not in {project["id"] for project in after["projects"]}
    assert after["pending_approvals"] == []
    assert after["pending_provider_actions"] == []
    hidden_row = next(project for project in with_hidden["projects"] if project["id"] == project_id)
    assert hidden_row["hidden"] is True
    assert restored["hidden"] is False
    assert project_id in {project["id"] for project in final["projects"]}


def test_dashboard_provider_health_uses_cache_without_probe(monkeypatch) -> None:
    import muxdev.services.dashboard_run as dashboard_module

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
    import muxdev.services.dashboard_run as dashboard_module

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


def test_daemon_provider_actions_api_and_continue_wait(monkeypatch) -> None:
    workspace = _workspace_temp("provider-action")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        run_dir = manager.paths.runs_dir / "run_provider_action"
        run_dir.mkdir(parents=True, exist_ok=True)
        with manager.board() as board:
            board.create_run(
                run_id="run_provider_action",
                task="provider action smoke",
                workflow="software-dev",
                provider="mock",
                workspace=workspace,
                worktree=workspace / "worktree",
            )
            action_id = board.create_provider_action(
                run_id="run_provider_action",
                stage_id="design",
                provider="codex",
                role="designer",
                kind="cli_confirmation",
                prompt_text="Apply this change? [y/N]",
                options=[{"label": "Yes", "value": "y"}, {"label": "No", "value": "n"}],
                transcript_path="transcript.log",
                chunks_path="chunks.jsonl",
                attach_command="muxdev attach run_provider_action --agent designer",
            )
        monkeypatch.setattr(manager, "_resume_task", lambda *_args, **_kwargs: None)
        client = TestClient(create_app(task_manager=manager))

        listed = client.get("/api/provider-actions?status=pending").json()
        task_listed = client.get("/api/tasks/run_provider_action/provider-actions?status=pending").json()
        detail = client.get("/api/tasks/run_provider_action").json()
        task_ux = client.get("/api/tasks/run_provider_action/ux").json()
        overview = client.get("/api/ux/overview").json()
        continued = client.post("/api/tasks/run_provider_action/continue").json()
        handled = client.post(f"/api/tasks/run_provider_action/actions/{action_id}/handled-and-continue").json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert listed[0]["action_id"] == action_id
    assert task_listed[0]["options"][0]["label"] == "Yes"
    assert detail["ux"]["user_state"] == "needs_action"
    assert task_ux["next_actions"][1]["kind"] == "mark_handled_continue"
    assert overview["action_center"][0]["kind"] == "provider_action"
    assert continued["status"] == "awaiting_provider_action"
    assert handled["action"]["status"] == str(ProviderActionStatus.HANDLED)
    assert handled["continue"]["status"] == "continue_requested"


def test_daemon_continue_does_not_start_duplicate_worker(monkeypatch) -> None:
    workspace = _workspace_temp("continue-dedupe")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        with manager.board() as board:
            board.create_run(
                run_id="run_duplicate_continue",
                task="duplicate continue",
                workflow="software-dev",
                provider="mock",
                workspace=workspace,
                worktree=workspace / "worktree",
            )

        def slow_resume(task_id: str, workspace: Path, *, max_cost_usd: float) -> None:
            time.sleep(0.5)

        monkeypatch.setattr(manager, "_resume_task", slow_resume)

        first = manager.continue_task("run_duplicate_continue")
        second = manager.continue_task("run_duplicate_continue")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert first["status"] == "continue_requested"
    assert second["status"] == "already_running"


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

        def provider_actions(self, *, status: str | None = None, task_id: str | None = None) -> list[dict[str, object]]:
            return [
                {
                    "action_id": "pact-1",
                    "run_id": task_id or "task-1",
                    "stage_id": "design",
                    "provider": "codex",
                    "kind": "cli_confirmation",
                    "status": status or "pending",
                    "prompt_text": "Apply this change? [y/N]",
                    "attach_command": "muxdev attach task-1 --agent designer",
                }
            ]

        def provider_action_handled(self, action_id: str) -> dict[str, object]:
            return {"action_id": action_id, "run_id": "task-1", "status": "handled"}

        def provider_action_dismiss(self, action_id: str) -> dict[str, object]:
            return {"action_id": action_id, "run_id": "task-1", "status": "dismissed"}

        def approve(self, approval_id: str) -> dict[str, object]:
            return {"approval_id": approval_id, "status": "approved"}

        def deny(self, approval_id: str) -> dict[str, object]:
            return {"approval_id": approval_id, "status": "denied"}

        def diff(self, task_id: str) -> dict[str, object]:
            return {"task_id": task_id, "diff": "diff --git a/x b/x", "path": "diff.patch"}

        def report(self, task_id: str) -> dict[str, object]:
            return {"task_id": task_id, "content": "# report", "path": "final_report.md"}

        def rollback(self, task_id: str, *, to_stage: str | None = None) -> dict[str, object]:
            return {"task_id": task_id, "status": "rolled_back", "to_stage": to_stage}

        def attach_command(self, task_id: str, *, agent: str = "implementer") -> dict[str, object]:
            return {"task_id": task_id, "agent": agent, "status": "attached", "handoff": {"command": ["tail", "-f", "trace.jsonl"]}}

    cli_main_module = importlib.import_module("muxdev.cli.main")
    monkeypatch.setattr(cli_main_module, "_daemon_client", lambda host="127.0.0.1", port=8788: FakeClient(host=host, port=port))

    run = runner.invoke(app, ["dev", "cli daemon smoke", "--json"])
    tasks = runner.invoke(app, ["tasks", "--json"])
    status = runner.invoke(app, ["status", "task-1", "--json"])
    approve = runner.invoke(app, ["approve", "appr-1", "--json"])
    actions = runner.invoke(app, ["actions", "--json"])
    handled = runner.invoke(app, ["action", "handled", "pact-1", "--json"])

    assert run.exit_code == 0
    assert json.loads(run.stdout)["task_id"] == "task-1"
    assert tasks.exit_code == 0
    assert json.loads(tasks.stdout)[0]["current_stage"] == "design"
    assert status.exit_code == 0
    assert json.loads(status.stdout)["run"]["run_id"] == "task-1"
    assert approve.exit_code == 0
    assert json.loads(approve.stdout)["status"] == "approved"
    assert actions.exit_code == 0
    assert json.loads(actions.stdout)[0]["action_id"] == "pact-1"
    assert handled.exit_code == 0
    assert json.loads(handled.stdout)["status"] == "handled"


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
        if status in {"completed", "blocked", "awaiting_approval", "awaiting_provider_action", "paused_budget", "aborted"}:
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
