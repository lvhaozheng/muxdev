from __future__ import annotations

import json
import importlib
import shutil
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.api.web import create_app
from muxdev.cli import app
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.process import daemon_health, daemon_status, start_daemon
from muxdev.daemon.tasks import TaskManager
from muxdev.models import ApprovalStatus, ProviderActionStatus, RunStatus
from muxdev.services.design import DESIGN_PACK_FILES
from muxdev.storage import MemoryStore


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
        (workspace / ".muxdev").mkdir()
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
        review_page = client.get(f"/review/{task_id}").text
        page = client.get("/").text
        english_page = client.get("/", params={"lang": "en"}).text
        task_english_page = client.get(f"/tasks/{task_id}", params={"lang": "en"}).text
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
    assert attach["handoff"]["mode"] == "transcript"
    assert attach["handoff"]["fallback_reason"]
    assert rollback["status"] in {"rolled_back", "failed"}
    assert "muxdev Shareable Run Review" in review_page
    assert "Sensitive transcript hidden" in review_page
    assert "daemon api smoke" in review_page
    assert "<title>muxdev 控制台</title>" in page
    assert "命令面板" in page
    assert "Command Palette" not in page
    assert "<title>muxdev Dashboard</title>" in english_page
    assert "Command Palette" in english_page
    assert f'data-task-id="{task_id}"' in task_english_page
    assert "Copy tmux command" not in english_page
    assert "design-memory" not in english_page
    assert "design-v2" not in task_english_page
    assert "dev-light" not in task_english_page
    assert "data-clamp" in task_english_page
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


def test_live_dashboard_renders_minimal_sections_and_i18n() -> None:
    from muxdev.api.web import render_live_dashboard_html

    html = render_live_dashboard_html("run_demo")
    english = render_live_dashboard_html("run_demo", lang="en")

    assert 'lang="zh-CN"' in html
    assert 'data-task-id="run_demo"' in html
    assert 'data-view-button="overview"' in html
    assert 'data-view-button="projects"' in html
    assert 'data-view-button="config"' in html
    assert "/dashboard/overview" in html
    assert "terminal?agent=" in html
    assert "feedback-and-continue" in html
    assert "submitPlanFeedback" in html
    assert "submitProviderResponse" in html
    assert "optionalTaskActions" in html
    assert "delivery_repair" in html
    assert 'data-feedback-optional="true"' in html
    assert "auto_submit:!optional" in html
    assert "data-draft-key" in html
    assert "refresh_deferred" in html
    assert "setInterval(()=>refresh(),30000)" in html
    assert "provider-actions" in html
    assert "Validation Experiments" not in html
    assert "Product Experience" not in html
    assert "Command Palette" not in html
    assert "<title>muxdev Dashboard</title>" in english
    assert 'lang="en"' in english
    assert "Ready CLI Tools" in english
    assert "Needs My Action" in english
    assert "Workflow Templates" in english
    assert "Delivery Repair" in english
    assert "Model Role / CLI Routing" in english
    assert "data-fold" in english


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
                json.dumps({"gate": "safe", "role_providers": {"code": "mock"}, "skills": [{"name": "docs-update", "role": "docs"}]}),
                encoding="utf-8",
            )
            with manager.board() as board:
                board.create_run(run_id=run_id, task=task, workflow="dev", provider="mock", workspace=project, worktree=project / ".muxdev" / run_id)
                board.set_run_status(run_id, "running")
                board.upsert_stage(run_id, "implement", role="code", status="running", summary="coding")
                if run_id == "run_dash_1":
                    snapshot = project / "snapshot.patch"
                    snapshot.write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")
                    board.add_test_result(run_id, "implement", True, "pytest", "passed")
                    board.add_snapshot(run_id, "implement", path=snapshot, patch_hash="sha256:test")
                if run_id == "run_dash_2":
                    board.add_error(run_id, "implement", "provider_exit", "temporary network error")

        with manager.board() as board:
            board.upsert_provider_learning(provider="mock", role="code", run_id="run_dash_1", attempts=2, successes=2, failures=0, human_actions=0, score=1.0)
            board.add_parallel_conflict(run_id="run_dash_1", stage_id="parallel_batch", stages=["code_a", "code_b"], files=["src/app.py"], severity="high")
            review_path = project / "semantic.json"
            review_path.write_text("{}", encoding="utf-8")
            board.add_semantic_merge_review(run_id="run_dash_1", decision="accept", patch_hash="sha256:abc", findings=[], path=review_path)
            board.add_multi_repo_orchestration(
                orchestration_id="mrepo_dash",
                run_id=None,
                workspace=project,
                mode="design",
                task="coordinate dashboard",
                status="planned",
                repos=[{"path": str(project), "workflow": "dev"}],
            )
        validation_dir = project / ".muxdev" / "runs" / "val_dash" / "validation"
        validation_dir.mkdir(parents=True, exist_ok=True)
        (validation_dir / "experiment.json").write_text(
            json.dumps(
                {
                    "experiment_id": "val_dash",
                    "suite": {"name": "dashboard"},
                    "strategies": ["direct_cli", "muxdev_multi_cli"],
                    "comparison": {"winner": "muxdev_multi_cli"},
                    "artifacts": {"report": str(project / "reports" / "validation" / "val_dash.md")},
                    "updated_at": "2026-06-19T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        with MemoryStore(project) as store:
            active = store.propose_claim(claim="Use pytest for tests", kind="test_command", role="test", confidence=0.9)
            store.approve(str(active["id"]))
            store.propose_claim(claim="Do not use pytest for tests", kind="test_command", role="test", confidence=0.4)

        monkeypatch.setattr(web_module, "_provider_health_payload", lambda: {"ready": ["mock"], "partial": [], "unavailable": [], "total": 1, "providers": []})
        client = TestClient(create_app(task_manager=manager))
        payload = client.get("/api/dashboard/overview", params={"workspace": str(project)}).json()
        light_payload = client.get("/api/dashboard/overview", params={"workspace": str(project), "include_global_config": "false"}).json()
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
    assert first["current_activity"] == "running stage implement"
    assert first["elapsed_seconds"] is not None
    assert first["stage_timeline"][0]["elapsed_seconds"] is not None
    assert first["delivery_confidence"]["tests"]["status"] == "passed"
    assert first["delivery_confidence"]["rollback"]["available"] is True
    failed = next(task for task in code_group["tasks"] if task["task_id"] == "run_dash_2")
    assert failed["error_summary"]["message"] == "temporary network error"
    recovery = next(item for item in payload["action_center"] if item["kind"] == "recovery")
    assert recovery["endpoint"] == "/api/tasks/run_dash_2/continue"
    assert "temporary network error" in recovery["why"]
    assert {column["id"] for column in payload["task_board"]} == {"todo", "running", "waiting", "needs_review", "done", "failed"}
    assert payload["delivery_confidence"]["items"][0]["task_id"] in {"run_dash_1", "run_dash_2"}
    assert {item["id"] for item in payload["health_strip"]} == {"daemon", "providers", "budget", "git", "skills", "memory"}
    assert payload["projects"][0]["health"]["status"] in {"blocked", "running", "needs_attention"}
    assert payload["projects"][0]["shared_state"]["label"] == "Shared State / Memory Board"
    assert payload["validation"]["summary"]["count"] == 1
    assert set(payload["standards"]) == {"trusted_delivery", "validation", "governance", "configuration"}
    trusted_standards = payload["standards"]["trusted_delivery"]
    assert trusted_standards["total"] == 7
    assert {"confidence", "tests", "review", "rollback", "artifacts", "budget", "human_attention"} <= {
        item["id"] for item in trusted_standards["items"]
    }
    confidence_standard = next(item for item in trusted_standards["items"] if item["id"] == "confidence")
    assert {"id", "label", "status", "current", "target", "evidence", "action"} <= set(confidence_standard)
    validation_standards = payload["standards"]["validation"]
    assert {"experiment_exists", "baseline_coverage", "score", "test_pass_rate", "evidence_confidence", "safety", "rollback_efficiency"} <= {
        item["id"] for item in validation_standards["items"]
    }
    assert next(item for item in validation_standards["items"] if item["id"] == "baseline_coverage")["status"] == "ready"
    assert payload["standards"]["configuration"]["items"]
    assert payload["standards"]["governance"]["items"]
    assert payload["governance_summary"]["validation"]["latest"]["experiment_id"] == "val_dash"
    assert payload["governance_summary"]["provider_learning"]["trend"][0]["provider"] == "mock"
    assert payload["governance_summary"]["parallel_control"]["open_conflicts"] == 1
    assert payload["governance_summary"]["multi_repo"]["count"] == 1
    assert payload["governance_summary"]["memory"]["counts"]["contradictions"] >= 1
    assert payload["global_config"]["role_routes"]
    assert all(route["role"] not in {"human_gate", "delivery_gate", "gate"} for route in payload["global_config"]["role_routes"])
    assert "plugin_market" not in payload["global_config"]
    assert payload["global_config"]["workflow_templates"]["templates"]
    assert payload["global_config"]["workflow_templates"]["templates"][0]["best_for"]
    assert all(not {"human_gate", "delivery_gate", "gate"} & set(row.get("roles", [])) for row in payload["global_config"]["workflow_templates"]["definitions"])
    assert "catalog" in payload["global_config"]["skills_catalog"]
    assert light_payload["global_config"] == {}
    assert light_payload["global_config_deferred"] is True
    mcp = payload["global_config"]["mcp"]
    assert mcp["status"] == "enabled"
    assert mcp["mode"] == "local stdio"
    assert mcp["tools_count"] > 0
    assert mcp["resources_count"] > 0
    assert mcp["prompts_count"] > 0
    assert mcp["write_policy"] == "guarded"
    assert len(mcp["recent_guardrails"]) <= 3


def test_dashboard_overview_treats_repaired_completed_deliverables_as_done() -> None:
    workspace = _workspace_temp("dashboard-repaired")
    try:
        project = workspace / "test_course"
        project.mkdir(parents=True)
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        run_id = "run_repaired_design"
        run_dir = project / ".muxdev" / "runs" / run_id
        design_dir = run_dir / "design"
        design_dir.mkdir(parents=True, exist_ok=True)
        docs_dir = project / "docs" / "design"
        docs_dir.mkdir(parents=True, exist_ok=True)
        design_doc = docs_dir / "design.md"
        design_doc.write_text("# 设计文档\n\n## 任务概览\n\n- 已修复并发布。\n", encoding="utf-8")
        for filename in DESIGN_PACK_FILES:
            (design_dir / filename).write_text(f"# {filename}\n\nready\n", encoding="utf-8")
        report = run_dir / "final_report.md"
        report.write_text("# Final Report\n\ncompleted\n", encoding="utf-8")
        with manager.board() as board:
            board.create_run(
                run_id=run_id,
                task="设计一个贪吃蛇游戏",
                workflow="design-lite",
                provider="codex",
                workspace=project,
                worktree=run_dir / "worktree",
            )
            board.set_run_status(run_id, RunStatus.COMPLETED)
            board.upsert_stage(run_id, "design_pack", role="docs", status="completed", summary="published")
            board.add_artifact(run_id, None, "Design Document", design_doc, "project_design_doc")
            board.add_artifact(run_id, None, "final_report.md", report, "report")
            board.add_error(run_id, None, "blind_validator_reject", "blind validator rejected the patch")
            board.add_review_blocker(
                run_id,
                "blind_validator",
                type="blind_validator_reject",
                file=None,
                line=None,
                severity="high",
                suggestion="historical blocker before deliverable repair",
            )

        client = TestClient(create_app(task_manager=manager))
        payload = client.get("/api/dashboard/overview", params={"workspace": str(project), "include_global_config": "false"}).json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert not any(item.get("run_id") == run_id or item.get("task_id") == run_id for item in payload["action_center"])
    project_payload = next(item for item in payload["projects"] if item["path"] == str(project.resolve()))
    assert project_payload["summary"]["failed"] == 0
    assert project_payload["health"]["status"] == "ready"
    task = next(item for item in _dashboard_tasks(payload) if item["task_id"] == run_id)
    assert task["status"] == "completed"
    assert task["errors"] == 0
    assert task["historical_errors"] == 1
    assert task["deliverable_status"]["complete"] is True
    assert task["risk"] == "low"
    assert task["delivery_confidence"]["label"] == "reviewable"
    assert task["delivery_confidence"]["review"]["status"] == "clear"


def test_dashboard_overview_groups_nested_design_workspace_under_project_root() -> None:
    workspace = _workspace_temp("dashboard-nested-project")
    try:
        project = workspace / "test_cource"
        nested = project / "docs" / "design"
        nested.mkdir(parents=True)
        (project / ".muxdev").mkdir()
        (nested / ".muxdev" / "runs").mkdir(parents=True)
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        with manager.board() as board:
            board.create_run(
                run_id="run_nested_design",
                task="根据本地的设计文档完成开发",
                workflow="dev",
                provider="codex",
                workspace=nested,
                worktree=nested / ".muxdev" / "runs" / "run_nested_design" / "worktree",
            )
            board.set_run_status("run_nested_design", RunStatus.BLOCKED)
            board.add_error("run_nested_design", "task_intake", "provider_exit", "temporary provider error")

        client = TestClient(create_app(task_manager=manager))
        payload = client.get("/api/dashboard/overview", params={"workspace": str(project), "include_global_config": "false"}).json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    project_names = {project["name"] for project in payload["projects"]}
    assert "test_cource" in project_names
    assert "design" not in project_names
    project_payload = next(project for project in payload["projects"] if project["name"] == "test_cource")
    task = next(task for task in _dashboard_tasks(payload) if task["task_id"] == "run_nested_design")
    assert task["workspace"] == str(nested.resolve())
    assert task["project_name"] == "test_cource"
    assert task["project_path"] == str(project.resolve())
    recovery = next(item for item in payload["action_center"] if item["task_id"] == "run_nested_design")
    assert recovery["project_id"] == project_payload["id"]
    assert recovery["project_name"] == "test_cource"


def test_dashboard_overview_keeps_unmarked_nested_workspace_as_project(monkeypatch) -> None:
    workspace = _workspace_temp("dashboard-unmarked-nested")
    try:
        import muxdev.core.projects as projects_module

        monkeypatch.setattr(projects_module, "_has_project_marker", lambda path: False)
        nested = workspace / "plain" / "docs" / "design"
        nested.mkdir(parents=True)
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        with manager.board() as board:
            board.create_run(
                run_id="run_unmarked_design",
                task="unmarked nested workspace",
                workflow="dev",
                provider="mock",
                workspace=nested,
                worktree=nested / ".muxdev" / "runs" / "run_unmarked_design" / "worktree",
            )
            board.set_run_status("run_unmarked_design", RunStatus.RUNNING)

        client = TestClient(create_app(task_manager=manager))
        payload = client.get("/api/dashboard/overview", params={"workspace": str(workspace), "include_global_config": "false"}).json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    project_payload = next(project for project in payload["projects"] if project["name"] == "design")
    assert project_payload["path"] == str(nested.resolve())
    task = next(task for task in _dashboard_tasks(payload) if task["task_id"] == "run_unmarked_design")
    assert task["project_name"] == "design"
    assert task["project_path"] == str(nested.resolve())


def test_task_submission_normalizes_nested_workspace_to_project_root() -> None:
    workspace = _workspace_temp("submit-nested-project")
    try:
        project = workspace / "test_cource"
        nested = project / "docs" / "design"
        nested.mkdir(parents=True)
        (project / ".git").mkdir()
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        client = TestClient(create_app(task_manager=manager))

        submitted = client.post(
            "/api/tasks",
            json={"task": "nested workspace smoke", "workspace": str(nested), "provider": "mock"},
        ).json()
        task_id = submitted["task_id"]
        _wait_for_terminal(client, task_id)
        detail = client.get(f"/api/tasks/{task_id}").json()
        project_task_context_exists = (project / ".muxdev" / "runs" / task_id / "task_context.json").exists()
        nested_run_exists = (nested / ".muxdev" / "runs" / task_id).exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert detail["run"]["workspace"] == str(project.resolve())
    assert detail["run"]["worktree"].startswith(str(project / ".muxdev"))
    assert project_task_context_exists
    assert not nested_run_exists


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
            board.add_error("run_project_a", "code", "provider_exit", "project a failed")

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
    assert not any(item.get("project_id") == project_id for item in after["action_center"])
    hidden_row = next(project for project in with_hidden["projects"] if project["id"] == project_id)
    assert hidden_row["hidden"] is True
    assert restored["hidden"] is False
    assert project_id in {project["id"] for project in final["projects"]}


def test_dashboard_task_hide_filters_action_center_without_hiding_project(monkeypatch) -> None:
    workspace = _workspace_temp("dashboard-task-hide")
    try:
        import muxdev.api.web as web_module

        project = workspace / "project-a"
        project.mkdir(parents=True)
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        with manager.board() as board:
            board.create_run(run_id="run_visible", task="keep visible", workflow="dev", provider="mock", workspace=project, worktree=project / ".muxdev" / "visible")
            board.set_run_status("run_visible", "running")
            board.create_run(run_id="run_failed", task="hide failed", workflow="dev", provider="mock", workspace=project, worktree=project / ".muxdev" / "failed")
            board.set_run_status("run_failed", "blocked")
            board.add_error("run_failed", "code", "provider_exit", "temporary network error")

        monkeypatch.setattr(web_module, "_provider_health_payload", lambda: {"ready": ["mock"], "partial": [], "unavailable": [], "total": 1, "providers": []})
        client = TestClient(create_app(task_manager=manager))
        before = client.get("/api/dashboard/overview", params={"workspace": str(project)}).json()
        hidden = client.delete("/api/dashboard/tasks/run_failed", params={"workspace": str(project)}).json()
        after = client.get("/api/dashboard/overview", params={"workspace": str(project)}).json()
        with_hidden = client.get("/api/dashboard/overview", params={"workspace": str(project), "include_hidden": "true"}).json()
        restored = client.post("/api/dashboard/tasks/run_failed/restore").json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    project_id = before["projects"][0]["id"]
    assert hidden["hidden"] is True
    assert hidden["project_id"] == project_id
    assert after["projects"][0]["id"] == project_id
    assert _dashboard_task_ids(after) == {"run_visible"}
    assert not any(item.get("task_id") == "run_failed" for item in after["action_center"])
    hidden_card = next(task for task in _dashboard_tasks(with_hidden) if task["task_id"] == "run_failed")
    assert hidden_card["hidden"] is True
    assert restored["hidden"] is False


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
        (workspace / ".muxdev").mkdir()
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

        decided = client.post(f"/api/approvals/{task_id}/approve").json()
        continued = client.post(f"/api/tasks/{task_id}/continue").json()
        _wait_for_status(client, task_id, "completed")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert decided["approval_id"] == approval["approval_id"]
    assert decided["status"] == str(ApprovalStatus.APPROVED)
    assert decided["decided_at"]
    assert continued["status"] == "continue_requested"


def test_daemon_plan_feedback_revises_and_requests_new_approval() -> None:
    workspace = _workspace_temp("approval-feedback")
    try:
        (workspace / ".muxdev").mkdir()
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        client = TestClient(create_app(task_manager=manager))
        submitted = client.post(
            "/api/tasks",
            json={
                "task": "daemon approval feedback smoke",
                "workspace": str(workspace),
                "provider": "mock",
                "require_approval": ["plan"],
            },
        ).json()
        task_id = submitted["task_id"]
        _wait_for_status(client, task_id, "awaiting_approval")
        original = client.get("/api/approvals?status=pending").json()[0]

        feedback = client.post(
            f"/api/approvals/{original['approval_id']}/feedback-and-continue",
            json={"feedback": "Please simplify the plan before coding."},
        ).json()
        _wait_for_status(client, task_id, "awaiting_approval")
        detail = client.get(f"/api/tasks/{task_id}").json()
        approvals = client.get("/api/approvals?status=pending").json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert feedback["approval"]["status"] == str(ApprovalStatus.FEEDBACK)
    assert "plan_revise" in feedback["approval"]["reset_stages"]
    assert feedback["continue"]["status"] == "continue_requested"
    assert approvals[0]["approval_id"] != original["approval_id"]
    stages = {row["stage_id"]: row["status"] for row in detail["stages"]}
    assert stages["approve_plan"] == "running"


def test_daemon_design_feedback_request_uses_plan_feedback_api() -> None:
    workspace = _workspace_temp("design-feedback")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        run_dir = manager.paths.runs_dir / "run_design_feedback"
        run_dir.mkdir(parents=True, exist_ok=True)
        with manager.board() as board:
            board.create_run(
                run_id="run_design_feedback",
                task="design compliance approval flow",
                workflow="design-lite",
                provider="mock",
                workspace=workspace,
                worktree=workspace / "worktree",
            )
            board.set_run_status("run_design_feedback", RunStatus.AWAITING_FEEDBACK)
            for stage_id, status in (
                ("design_brief", "running"),
                ("design_review", "completed"),
                ("design_verify", "completed"),
                ("approve_plan", "running"),
                ("design_pack", "completed"),
            ):
                board.upsert_stage("run_design_feedback", stage_id, role="architect" if stage_id == "design_brief" else "review", status=status)
            request_id = board.add_feedback_event(
                run_id="run_design_feedback",
                source="provider",
                kind="design_feedback_request",
                severity="medium",
                status="pending",
                route_to="architect",
                content="Which compliance region should this flow satisfy?",
                payload={"stage_id": "design_brief", "workflow": "design-lite", "prompt_source": "delivery_decision"},
            )
        client = TestClient(create_app(task_manager=manager))

        overview = client.get("/api/ux/overview").json()
        blocked_continue = client.post("/api/tasks/run_design_feedback/continue").json()
        result = client.post(
            "/api/feedback",
            json={
                "kind": "plan_feedback",
                "source": "dashboard",
                "content": "Use US SOC2 assumptions.",
                "run_id": "run_design_feedback",
                "auto_submit": False,
            },
        ).json()
        detail = client.get("/api/tasks/run_design_feedback").json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert overview["action_center"][0]["kind"] == "plan_feedback"
    assert overview["action_center"][0]["feedback_id"] == request_id
    assert blocked_continue["status"] == str(RunStatus.AWAITING_FEEDBACK)
    assert blocked_continue["feedback_requests"][0]["feedback_id"] == request_id
    assert result["handled_feedback_requests"] == [request_id]
    assert result["reset_stages"] == ["design_brief", "design_review", "design_verify", "approve_plan", "design_pack"]
    assert detail["run"]["status"] == str(RunStatus.RUNNING)
    events = {row["feedback_id"]: row for row in detail["feedback_events"]}
    assert events[request_id]["status"] == "handled"
    assert any(row["kind"] == "plan_feedback" and row["content"] == "Use US SOC2 assumptions." for row in detail["feedback_events"])
    stages = {row["stage_id"]: row["status"] for row in detail["stages"]}
    assert stages["design_brief"] == "pending"
    assert stages["design_review"] == "pending"


def test_daemon_provider_actions_api_and_continue_wait(monkeypatch) -> None:
    workspace = _workspace_temp("provider-action")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        run_dir = manager.paths.runs_dir / "run_provider_action"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "trace.jsonl").write_text('{"type":"trace event","data":{"message":"hello cli"}}\n', encoding="utf-8")
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
        terminal = client.get("/tasks/run_provider_action/terminal?agent=designer")
        continued = client.post("/api/tasks/run_provider_action/continue").json()
        responded = client.post(f"/api/provider-actions/{action_id}/response", json={"choice": "y"}).json()
        handled = client.post(f"/api/tasks/run_provider_action/actions/{action_id}/handled-and-continue").json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert listed[0]["action_id"] == action_id
    assert task_listed[0]["options"][0]["label"] == "Yes"
    assert task_listed[0]["input_kind"] == "confirmation"
    assert task_listed[0]["choices"][0]["label"] == "Yes"
    assert detail["ux"]["user_state"] == "needs_action"
    assert task_ux["next_actions"][1]["kind"] == "mark_handled_continue"
    assert overview["action_center"][0]["kind"] == "provider_action"
    assert terminal.status_code == 200
    assert "Transcript fallback" in terminal.text
    assert "provider action smoke" in terminal.text
    assert "agent designer" in terminal.text
    assert "trace event" in terminal.text
    assert continued["status"] == "awaiting_provider_action"
    assert responded["response"] == {"choice": "y"}
    assert handled["action"]["status"] == str(ProviderActionStatus.HANDLED)
    assert handled["continue"]["status"] == "continue_requested"


def test_daemon_provider_action_respond_and_continue_starts_resume(monkeypatch) -> None:
    workspace = _workspace_temp("provider-action-submit")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        with manager.board() as board:
            board.create_run(
                run_id="run_provider_action_submit",
                task="provider action submit smoke",
                workflow="design-lite",
                provider="mock",
                workspace=workspace,
                worktree=workspace / "worktree",
            )
            action_id = board.create_provider_action(
                run_id="run_provider_action_submit",
                stage_id="design_brief",
                provider="codex",
                role="architect",
                kind="cli_confirmation",
                prompt_text="Confirm design preferences",
                input_kind="text",
            )
        resume_calls: list[tuple[str, float]] = []

        def record_resume(task_id: str, _workspace: Path, *, max_cost_usd: float) -> None:
            resume_calls.append((task_id, max_cost_usd))

        monkeypatch.setattr(manager, "_resume_task", record_resume)
        client = TestClient(create_app(task_manager=manager))

        result = client.post(
            f"/api/tasks/run_provider_action_submit/actions/{action_id}/respond-and-continue",
            json={"response": {"text": "platform=Web; style=cartoon"}},
        ).json()
        pending = client.get("/api/tasks/run_provider_action_submit/provider-actions?status=pending").json()

        deadline = time.time() + 1
        while not resume_calls and time.time() < deadline:
            time.sleep(0.01)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert result["action"]["status"] == str(ProviderActionStatus.HANDLED)
    assert result["action"]["response"] == {"text": "platform=Web; style=cartoon"}
    assert result["continue"]["status"] == "continue_requested"
    assert pending == []
    assert resume_calls == [("run_provider_action_submit", 0.5)]


def test_daemon_continue_skips_false_positive_provider_action(monkeypatch) -> None:
    workspace = _workspace_temp("provider-action-false-positive")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        with manager.board() as board:
            board.create_run(
                run_id="run_provider_action_false_positive",
                task="provider action false positive smoke",
                workflow="design-lite",
                provider="mock",
                workspace=workspace,
                worktree=workspace / "worktree",
            )
            action_id = board.create_provider_action(
                run_id="run_provider_action_false_positive",
                stage_id="design_brief",
                provider="codex",
                role="architect",
                kind="cli_confirmation",
                prompt_text=(
                    '{"task":{"provider_action_responses":[{"prompt_text":"waiting_external_confirmation: old",'
                    '"response":{"choice":"yes"}}],"context_packet_hash":"sha256:test","worktree":"D:\\\\demo"}}\n'
                    "D:\\anconda\\Lib\\site-packages\\requests\\__init__.py:113: RequestsDependencyWarning: mismatch\n"
                    "  warnings.warn(\n"
                    '{"exit_code":0,"status":"completed"}'
                ),
                input_kind="confirmation",
            )
        resume_calls: list[str] = []

        def record_resume(task_id: str, _workspace: Path, *, max_cost_usd: float) -> None:
            resume_calls.append(task_id)

        monkeypatch.setattr(manager, "_resume_task", record_resume)
        client = TestClient(create_app(task_manager=manager))

        result = client.post("/api/tasks/run_provider_action_false_positive/continue").json()
        deadline = time.time() + 1
        while not resume_calls and time.time() < deadline:
            time.sleep(0.01)
        actions = client.get("/api/tasks/run_provider_action_false_positive/provider-actions").json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert result["status"] == "continue_requested"
    assert action_id in result["dismissed_provider_actions"]
    assert resume_calls == ["run_provider_action_false_positive"]
    assert actions[0]["status"] == str(ProviderActionStatus.DISMISSED)


def test_daemon_continue_marks_recovering_task_running(monkeypatch) -> None:
    workspace = _workspace_temp("continue-running-status")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        with manager.board() as board:
            board.create_run(
                run_id="run_blocked_continue",
                task="blocked continue smoke",
                workflow="design-lite",
                provider="mock",
                workspace=workspace,
                worktree=workspace / "worktree",
            )
            board.set_run_status("run_blocked_continue", RunStatus.BLOCKED)
        resume_calls: list[str] = []

        def record_resume(task_id: str, _workspace: Path, *, max_cost_usd: float) -> None:
            resume_calls.append(task_id)

        monkeypatch.setattr(manager, "_resume_task", record_resume)

        result = manager.continue_task("run_blocked_continue")
        run = manager.get_run("run_blocked_continue")
        deadline = time.time() + 1
        while not resume_calls and time.time() < deadline:
            time.sleep(0.01)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert result["status"] == "continue_requested"
    assert run["status"] == str(RunStatus.RUNNING)
    assert resume_calls == ["run_blocked_continue"]


def test_daemon_attach_uses_real_provider_attach_command() -> None:
    workspace = _workspace_temp("native-attach")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        with manager.board() as board:
            board.create_run(
                run_id="run_native_attach",
                task="native attach smoke",
                workflow="dev",
                provider="codex",
                workspace=workspace,
                worktree=workspace / "worktree",
            )
            board.create_provider_action(
                run_id="run_native_attach",
                stage_id="plan",
                provider="codex",
                role="plan",
                kind="cli_confirmation",
                prompt_text="Continue?",
                attach_command="codex session attach abc123",
            )
        handoff = manager.attach_command("run_native_attach", agent="plan")["handoff"]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert handoff["mode"] == "native_cli"
    assert handoff["command"] == "codex session attach abc123"


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

        def provider_action_response(self, action_id: str, response: object) -> dict[str, object]:
            return {"action_id": action_id, "run_id": "task-1", "status": "handled", "response": response}

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
    responded = runner.invoke(app, ["action", "respond", "pact-1", "--choice", "yes", "--json"])

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
    assert responded.exit_code == 0
    assert json.loads(responded.stdout)["response"] == {"choice": "yes"}


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


def test_daemon_health_tolerates_slow_local_health_response() -> None:
    class SlowHealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            time.sleep(0.6)
            body = json.dumps({"service": "muxdev", "status": "ok"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = daemon_health(host="127.0.0.1", api_port=server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert payload["ok"] is True


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
        monkeypatch.setattr(process_module, "_listening_pids_for_ports", lambda ports: [])
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


def test_start_daemon_reports_port_conflict_without_spawning(monkeypatch) -> None:
    import muxdev.daemon.process as process_module

    workspace = _workspace_temp("process-port-conflict")
    try:
        paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
        monkeypatch.setattr(process_module, "daemon_health", lambda **kwargs: {"ok": False, "error": "connection refused"})
        monkeypatch.setattr(process_module, "_listening_pids_for_ports", lambda ports: [24680])

        def fail_popen(*args, **kwargs):  # pragma: no cover - should never be reached
            raise AssertionError("start_daemon should not spawn when dashboard ports are occupied")

        monkeypatch.setattr(process_module.subprocess, "Popen", fail_popen)
        payload = process_module.start_daemon(paths=paths, api_port=8788, ui_port=8787)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["running"] is False
    assert payload["started"] is False
    assert payload["error"] == "port_conflict"
    assert payload["pids"] == [24680]


def test_stop_daemon_can_stop_health_only_daemon_by_port(monkeypatch) -> None:
    import muxdev.daemon.process as process_module

    workspace = _workspace_temp("process-port-stop")
    killed: list[int] = []
    try:
        paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
        paths.pid_path.unlink(missing_ok=True)
        monkeypatch.setattr(process_module, "daemon_health", lambda **kwargs: {"ok": True})
        port_pid_snapshots = [[24680], []]
        monkeypatch.setattr(process_module, "_listening_pids_for_ports", lambda ports: port_pid_snapshots.pop(0) if port_pid_snapshots else [])
        monkeypatch.setattr(process_module, "is_pid_alive", lambda pid: False)
        monkeypatch.setattr(process_module, "_terminate_pid", lambda pid: killed.append(pid))
        monkeypatch.setattr(process_module, "wait_for_daemon_stop", lambda **kwargs: True)

        payload = process_module.stop_daemon(paths=paths, api_port=8788, ui_port=8787)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["stopped"] is True
    assert payload["pids"] == [24680]
    assert killed == [24680]


def test_stop_daemon_stops_pid_and_dashboard_port_owner(monkeypatch) -> None:
    import muxdev.daemon.process as process_module

    workspace = _workspace_temp("process-stop-port-owner")
    killed: list[int] = []
    live_pids = {13579}
    try:
        paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
        paths.pid_path.write_text("13579", encoding="utf-8")
        monkeypatch.setattr(process_module, "is_pid_alive", lambda pid: pid in live_pids)
        port_pid_snapshots = [[24680], []]
        monkeypatch.setattr(process_module, "_listening_pids_for_ports", lambda ports: port_pid_snapshots.pop(0) if port_pid_snapshots else [])

        def fake_terminate(pid: int) -> None:
            killed.append(pid)
            live_pids.discard(pid)

        monkeypatch.setattr(process_module, "_terminate_pid", fake_terminate)
        monkeypatch.setattr(process_module, "wait_for_daemon_stop", lambda **kwargs: True)

        payload = process_module.stop_daemon(paths=paths, api_port=8788, ui_port=8787)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["stopped"] is True
    assert payload["pids"] == [13579, 24680]
    assert payload["port_pids"] == [24680]
    assert killed == [13579, 24680]


def test_stop_daemon_reports_surviving_port_owner(monkeypatch) -> None:
    import muxdev.daemon.process as process_module

    workspace = _workspace_temp("process-stop-survivor")
    killed: list[int] = []
    try:
        paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
        paths.pid_path.unlink(missing_ok=True)
        monkeypatch.setattr(process_module, "_listening_pids_for_ports", lambda ports: [24680])
        monkeypatch.setattr(process_module, "_terminate_pid", lambda pid: killed.append(pid))
        monkeypatch.setattr(process_module, "wait_for_daemon_stop", lambda **kwargs: False)
        monkeypatch.setattr(process_module, "_wait_for_pids_exit", lambda pids: False)

        payload = process_module.stop_daemon(paths=paths, api_port=8788, ui_port=8787)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["running"] is True
    assert payload["stopped"] is False
    assert payload["pids"] == [24680]
    assert payload["remaining_port_pids"] == [24680]
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


def _dashboard_tasks(payload: dict[str, object]) -> list[dict[str, object]]:
    tasks: list[dict[str, object]] = []
    for project in payload.get("projects", []):
        if not isinstance(project, dict):
            continue
        for workflow in project.get("workflows", []):
            if not isinstance(workflow, dict):
                continue
            for group in workflow.get("role_groups", []):
                if not isinstance(group, dict):
                    continue
                tasks.extend(task for task in group.get("tasks", []) if isinstance(task, dict))
    return tasks


def _dashboard_task_ids(payload: dict[str, object]) -> set[str]:
    return {str(task.get("task_id") or task.get("run_id") or "") for task in _dashboard_tasks(payload)}


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
