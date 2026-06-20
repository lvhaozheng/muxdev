from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.api.mcp import handle_jsonrpc, mcp_doctor, server_manifest
from muxdev.api.web import create_app, render_dashboard_html, render_live_dashboard_html
from muxdev.cli import app
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.services.skills import write_skill_lock
from muxdev.storage import Blackboard


runner = CliRunner()


def test_feedback_router_submits_ci_rescue_and_records_cache() -> None:
    workspace = _workspace_temp("p3-feedback")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        client = TestClient(create_app(task_manager=manager))

        routed = client.post(
            "/api/feedback",
            json={
                "kind": "ci_failed",
                "source": "github-actions",
                "content": "pytest failed in tests/test_login.py",
                "workspace": str(workspace),
                "provider": "mock",
            },
        ).json()
        ecosystem = client.get("/api/ecosystem").json()

        assert routed["auto"] is True
        assert routed["route_to"] == "test"
        assert routed["submitted"]["run_id"].startswith("run_")
        assert ecosystem["feedback_events"][0]["kind"] == "ci_failed"
        assert ecosystem["ci_rescues"][0]["rescue_run_id"] == routed["submitted"]["run_id"]
        assert ecosystem["cache_entries"][0]["kind"] == "feedback_event"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_dashboard_feedback_infers_workspace_from_run_id() -> None:
    workspace = _workspace_temp("p3-feedback-run")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        client = TestClient(create_app(task_manager=manager))
        with manager.board() as board:
            board.create_run(
                run_id="run_feedback",
                task="design game",
                workflow="dev",
                provider="mock",
                workspace=workspace,
                worktree=workspace / ".muxdev" / "runs" / "run_feedback" / "worktree",
            )

        routed = client.post(
            "/api/feedback",
            json={
                "kind": "manual_feedback",
                "source": "dashboard",
                "content": "make the game playable with keyboard controls",
                "run_id": "run_feedback",
                "auto_submit": False,
            },
        ).json()
        ecosystem = client.get("/api/ecosystem").json()

        assert routed["auto"] is False
        assert routed["status"] == "needs_review"
        assert ecosystem["feedback_events"][0]["run_id"] == "run_feedback"
        assert ecosystem["feedback_events"][0]["source"] == "dashboard"
        cache_path = Path(str(ecosystem["cache_entries"][0]["path"]))
        assert workspace in cache_path.parents
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_skill_lock_writes_lock_and_optional_skill_memory() -> None:
    workspace = _workspace_temp("p3-skill-lock")
    try:
        skill_dir = workspace / "skills" / "reviewer"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: reviewer\nversion: 1.2.3\ncompatible_roles: [review, secure]\n---\n# Reviewer\n",
            encoding="utf-8",
        )

        payload = write_skill_lock(workspace, promote_memory=True)

        assert Path(payload["path"]).exists()
        reviewer = next(row for row in payload["skills"] if row["name"] == "reviewer")
        assert reviewer["version"] == "1.2.3"
        assert "review" in reviewer["compatible_roles"]
        assert payload["memory_proposals"]
        assert payload["memory_proposals"][0]["kind"] == "skill_memory"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_mcp_guardrail_tools_record_events() -> None:
    workspace = _workspace_temp("p3-mcp")
    try:
        initialized = handle_jsonrpc({"jsonrpc": "2.0", "id": 0, "method": "initialize"}, workspace)
        assert initialized["result"]["capabilities"]["resources"] == {}
        assert initialized["result"]["capabilities"]["prompts"] == {}
        tool_names = {tool["name"] for tool in server_manifest()["tools"]}
        assert "muxdev.check_policy" in tool_names
        assert "workflow.templates" in tool_names
        assert "workflow.plugins" in tool_names
        assert "muxdev.submit_task" in tool_names
        templates = handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "workflow.templates", "arguments": {}}},
            workspace,
        )
        assert templates["result"]["content"][0]["json"]
        resources = handle_jsonrpc({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, workspace)
        assert "muxdev://workspace/summary" in {row["uri"] for row in resources["result"]["resources"]}
        resource = handle_jsonrpc({"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "muxdev://workspace/summary"}}, workspace)
        assert "workspace" in resource["result"]["contents"][0]["text"]
        prompts = handle_jsonrpc({"jsonrpc": "2.0", "id": 5, "method": "prompts/list"}, workspace)
        assert "muxdev.workflow.design" in {row["name"] for row in prompts["result"]["prompts"]}
        prompt = handle_jsonrpc({"jsonrpc": "2.0", "id": 6, "method": "prompts/get", "params": {"name": "muxdev.workflow.design", "arguments": {"task": "ship it"}}}, workspace)
        assert "ship it" in prompt["result"]["messages"][0]["content"]["text"]
        disabled = handle_jsonrpc({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "muxdev.submit_task", "arguments": {"task": "x"}}}, workspace)
        assert disabled["error"]["code"] == -32000
        assert "disabled" in disabled["error"]["message"]
        response = handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "muxdev.check_policy", "arguments": {"command": "rm -rf /"}},
            },
            workspace,
        )

        payload = response["result"]["content"][0]["json"]
        assert payload["decision"] == "deny"
        with Blackboard(workspace / ".muxdev", db_path=workspace / ".muxdev" / "ecosystem.sqlite") as board:
            events = board.table_rows("guardrail_events")
        assert events[0]["tool"] == "muxdev.check_policy"
        assert events[0]["decision"] == "deny"
        doctor = mcp_doctor(workspace)
        assert doctor["tools_count"] > 0
        assert doctor["resources_count"] > 0
        assert doctor["prompts_count"] > 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_mcp_allowlist_filters_and_rejects_tools() -> None:
    workspace = _workspace_temp("p3-mcp-allowlist")
    try:
        config_dir = workspace / ".muxdev"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text(
            '[mcp]\nallowed_tools = ["workflow.templates"]\nallowed_resources = ["muxdev://workspace/summary"]\n',
            encoding="utf-8",
        )
        listed = handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, workspace)
        assert {row["name"] for row in listed["result"]["tools"]} == {"workflow.templates"}
        denied = handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "muxdev.check_policy", "arguments": {"command": "rm -rf /"}}},
            workspace,
        )
        assert "not allowed" in denied["error"]["message"]
        resources = handle_jsonrpc({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, workspace)
        assert {row["uri"] for row in resources["result"]["resources"]} == {"muxdev://workspace/summary"}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_mcp_disabled_hides_surface_and_rejects_calls() -> None:
    workspace = _workspace_temp("p3-mcp-disabled")
    try:
        config_dir = workspace / ".muxdev"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text("[mcp]\nenabled = false\n", encoding="utf-8")

        manifest = server_manifest(workspace)
        assert manifest["tools"] == []
        assert manifest["resources"] == []
        assert manifest["prompts"] == []

        listed = handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, workspace)
        assert listed["result"]["tools"] == []
        resources = handle_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "resources/list"}, workspace)
        assert resources["result"]["resources"] == []
        denied = handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "workflow.templates", "arguments": {}}},
            workspace,
        )
        assert "MCP is disabled" in denied["error"]["message"]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_cli_skill_lock_and_removed_plugin_command() -> None:
    workspace = _workspace_temp("p3-cli")
    try:
        skill_dir = workspace / "skills" / "docs"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: docs\nversion: 0.1\n---\n# Docs\n", encoding="utf-8")

        import os

        previous = Path.cwd()
        os.chdir(workspace)
        try:
            locked = runner.invoke(app, ["skill", "lock", "--no-memory", "--json"])
            removed = runner.invoke(app, ["plugin", "validate", "anything", "--json"])
        finally:
            os.chdir(previous)

        assert locked.exit_code == 0
        assert removed.exit_code != 0
        assert json.loads(removed.stdout)["status"] == "removed"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_dashboard_renders_p3_ecosystem_sections() -> None:
    html = render_dashboard_html(
        {
            "app": {"workspace": "workspace"},
            "run": {"run_id": "run_p3", "task": "p3", "status": "running"},
            "summary": {},
            "stages": [],
            "agents": [],
            "approvals": [],
            "provider_actions": [],
            "provider_attempts": [],
            "session_capsules": [],
            "feedback_events": [{"feedback_id": "fb_1", "kind": "ci_failed", "status": "routed"}],
            "ci_rescues": [{"rescue_id": "cires_1", "status": "submitted"}],
            "cache_entries": [{"cache_key": "sha256:abc", "kind": "feedback_event"}],
            "skill_locks": [{"skill_name": "reviewer", "status": "locked"}],
            "memory_context": [{"id": "mem_1", "claim": "use pytest"}],
            "guardrail_events": [{"event_id": "guard_1", "decision": "deny"}],
            "test_results": [],
            "review_blockers": [],
            "errors": [],
            "artifacts": [],
            "usage": [],
            "trace": [],
        }
    )
    live = render_live_dashboard_html()

    assert "Feedback Router" in html
    assert "CI Rescue" in html
    assert "CAS Cache" in html
    assert "Skill Lock" in html
    assert "Plugin Manifest" not in html
    assert "记忆上下文" in live
    assert "角色会话" in live
    assert "工作流模板" in live
    english_live = render_live_dashboard_html(lang="en")
    assert "Memory Context" in english_live
    assert "Role Sessions" in english_live
    assert "Workflow Templates" in english_live


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
