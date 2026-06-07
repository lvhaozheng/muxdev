from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.api.mcp import handle_jsonrpc, server_manifest
from muxdev.api.web import create_app, render_dashboard_html, render_live_dashboard_html
from muxdev.cli import app
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.services.plugin_manifest import validate_plugin_manifest
from muxdev.services.skill_lock import write_skill_lock
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


def test_safe_plugin_manifest_flags_sensitive_permissions() -> None:
    workspace = _workspace_temp("p3-plugin")
    try:
        plugin_dir = workspace / "plugin"
        manifest_dir = plugin_dir / ".codex-plugin"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "plugin.json").write_text(
            json.dumps({"name": "danger-plugin", "permissions": ["read", "shell", "network:api"]}),
            encoding="utf-8",
        )

        payload = validate_plugin_manifest(str(plugin_dir))

        assert payload["name"] == "danger-plugin"
        assert payload["status"] == "needs_review"
        assert payload["warnings"]
        assert "shell" in ", ".join(payload["warnings"])
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_mcp_guardrail_tools_record_events() -> None:
    workspace = _workspace_temp("p3-mcp")
    try:
        assert "muxdev.check_policy" in {tool["name"] for tool in server_manifest()["tools"]}
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
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_cli_skill_lock_and_plugin_validate() -> None:
    workspace = _workspace_temp("p3-cli")
    try:
        skill_dir = workspace / "skills" / "docs"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: docs\nversion: 0.1\n---\n# Docs\n", encoding="utf-8")
        plugin_dir = workspace / "plugin"
        (plugin_dir / ".codex-plugin").mkdir(parents=True)
        (plugin_dir / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "docs-plugin", "permissions": ["read"]}), encoding="utf-8")

        import os

        previous = Path.cwd()
        os.chdir(workspace)
        try:
            locked = runner.invoke(app, ["skill", "lock", "--no-memory", "--json"])
            validated = runner.invoke(app, ["plugin", "validate", str(plugin_dir), "--json"])
        finally:
            os.chdir(previous)

        assert locked.exit_code == 0
        assert validated.exit_code == 0
        assert json.loads(validated.stdout)["status"] == "trusted"
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
            "plugin_manifests": [{"plugin_name": "plugin", "status": "trusted"}],
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
    assert "Plugin Manifest" in html
    assert "Memory Context" in live
    assert "Role Sessions" in live


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
