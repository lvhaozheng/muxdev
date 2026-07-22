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
    assert {
        "/api/v2/agents",
        "/api/v2/conversations",
        "/api/v2/conversations/{conversation_id}/messages",
        "/api/v2/conversations/{conversation_id}/orchestration/approve",
        "/api/v2/assignments/{assignment_id}/retry",
        "/api/v2/sessions/{session_id}/terminal",
    } <= paths
    assert len(product_routes) == 47
    assert len(server_manifest()["tools"]) == 8
    assert _leaf_count(get_command(app)) == 40
    tools = server_manifest()["tools"]
    assert all(item["inputSchema"].get("additionalProperties") is False for item in tools)


def test_http_and_mcp_read_surfaces(workspace: Path) -> None:
    client = TestClient(create_app(workspace))
    assert client.get("/health").json()["tables"] == 22
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
