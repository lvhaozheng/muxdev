from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from typer.main import get_command

from muxdev.api import create_app, handle_jsonrpc, server_manifest
from muxdev.cli import app


def _leaf_count(command) -> int:
    children = getattr(command, "commands", None)
    return sum(_leaf_count(item) for item in children.values()) if children else 1


def test_interface_budgets_are_fixed(workspace: Path) -> None:
    application = create_app(workspace)
    product_routes = [route for route in application.routes if getattr(route, "path", "") not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}]
    assert len(product_routes) == 18
    assert len(server_manifest()["tools"]) == 8
    assert _leaf_count(get_command(app)) == 29


def test_http_and_mcp_read_surfaces(workspace: Path) -> None:
    client = TestClient(create_app(workspace))
    assert client.get("/health").json()["tables"] == 12
    response = handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, workspace=workspace)
    assert len(response["result"]["tools"]) == 8
