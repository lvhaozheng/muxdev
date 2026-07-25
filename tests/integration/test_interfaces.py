from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from typer.main import get_command

from muxdev.api import create_app, handle_jsonrpc, server_manifest
from muxdev.cli import app


def _leaf_count(command) -> int:
    children = getattr(command, "commands", None)
    return sum(_leaf_count(item) for item in children.values()) if children else 1


def test_legacy_interfaces_remain_and_versioned_conversation_contract_is_explicit(workspace: Path) -> None:
    application = create_app(workspace)
    product_routes = [route for route in application.routes if getattr(route, "path", "") not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}]
    paths = {route.path for route in product_routes}
    assert {"/runs", "/runs/{run_id}", "/runs/{run_id}/evidence", "/interactions/{interaction_id}/respond"} <= paths
    assert {
        "/api/v1/conversations",
        "/api/v1/conversations/{conversation_id}/messages",
        "/api/v1/conversations/{conversation_id}/actions",
        "/api/v1/conversations/{conversation_id}/events",
        "/api/v1/deliveries/{candidate_id}/evidence",
        "/api/v1/deliveries/{candidate_id}/accept",
        "/api/v1/auth/pair",
    } <= paths
    project = "/api/v2/projects/{project_id}"
    assert {
        "/api/v2/projects",
        "/api/v2/rules",
        f"{project}/agents",
        f"{project}/conversations",
        f"{project}/conversations/{{conversation_id}}/messages",
        f"{project}/conversations/{{conversation_id}}/execute",
        f"{project}/conversations/{{conversation_id}}/interactions/{{interaction_id}}/respond",
        f"{project}/conversations/{{conversation_id}}/orchestration/approve",
        f"{project}/assignments/{{assignment_id}}/retry",
        f"{project}/sessions/{{session_id}}/resume",
        f"{project}/sessions/{{session_id}}/restart",
        f"{project}/sessions/{{session_id}}/interrupt",
        f"{project}/sessions/{{session_id}}/terminal",
        f"{project}/conversations/{{conversation_id}}/snapshot",
        f"{project}/conversations/{{conversation_id}}/stream",
        f"{project}/conversations/{{conversation_id}}/changes",
        f"{project}/conversations/{{conversation_id}}/file",
        f"{project}/conversations/{{conversation_id}}/review",
        f"{project}/conversations/{{conversation_id}}/replay",
        f"{project}/sessions/{{session_id}}/preview",
        f"{project}/deliveries/{{candidate_id}}/discard",
        } <= paths
    assert "/api/v2/conversations" not in paths
    assert f"{project}/conversations/{{conversation_id}}/room" not in paths
    assert len(product_routes) >= 59
    assert len(server_manifest()["tools"]) == 8
    assert _leaf_count(get_command(app)) == 46
    tools = server_manifest()["tools"]
    assert all(item["inputSchema"].get("additionalProperties") is False for item in tools)


def test_http_and_mcp_read_surfaces(workspace: Path) -> None:
    client = TestClient(create_app(workspace))
    assert client.get("/health").json()["tables"] == 31
    response = handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, workspace=workspace)
    assert len(response["result"]["tools"]) == 8


def test_resume_surfaces_share_the_bounded_recovery_actions(workspace: Path) -> None:
    manifest = server_manifest()
    resume = next(item for item in manifest["tools"] if item["name"] == "muxdev_resume_run")
    action = resume["inputSchema"]["properties"]["action"]

    assert set(action["enum"]) == {"auto", "fix-output", "retry", "switch-provider"}
    client = TestClient(create_app(workspace))
    assert client.post("/runs/missing/resume", json={"action": "unsafe"}).status_code == 422
    invalid = handle_jsonrpc({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "muxdev_resume_run", "arguments": {"run_id": "x", "action": "unsafe"}},
    }, workspace=workspace)
    assert "error" in invalid
