"""Minimal JSON-RPC/MCP-compatible control surface.

The MCP server exposes read-oriented muxdev capabilities to external agents:
provider discovery, workspace search, local RAG, workflow plugins, command
rendering, and flow listing. Tool handlers reuse the same services as the CLI so
there is one behavior source for humans, scripts, and agent clients.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import __version__
from ..config.loader import load_config
from ..providers import detect_providers
from ..services.flows import FlowRegistry
from ..services.rag import LocalRagIndex
from ..services.workflow_plugins import list_workflow_plugins, render_plugin_command


def server_manifest() -> dict[str, Any]:
    """Return the tool manifest advertised to MCP clients."""
    return {
        "name": "muxdev",
        "version": __version__,
        "protocol": "jsonrpc-2.0",
        "tools": [
            {
                "name": "provider.detect",
                "description": "Return muxdev provider capability probes.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "workspace.search",
                "description": "Search local workspace text.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "rag.query",
                "description": "Query the local muxdev retrieval index.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "workflow.plugins",
                "description": "List muxdev workflow plugin definitions.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "workflow.render",
                "description": "Render a workflow plugin phase command for a provider.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "plugin": {"type": "string"},
                        "phase": {"type": "string"},
                        "provider": {"type": "string"},
                        "task": {"type": "string"},
                    },
                    "required": ["plugin", "phase", "provider"],
                },
            },
            {
                "name": "flow.list",
                "description": "List local muxdev scheduled flow definitions.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ],
    }


def handle_jsonrpc(request: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Handle one JSON-RPC request and wrap exceptions as protocol errors."""
    method = request.get("method")
    request_id = request.get("id")
    try:
        if method == "initialize":
            result = {"serverInfo": {"name": "muxdev", "version": __version__}, "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": server_manifest()["tools"]}
        elif method == "tools/call":
            params = request.get("params") or {}
            result = _call_tool(str(params.get("name")), params.get("arguments") or {}, workspace)
        else:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}


def _call_tool(name: str, arguments: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Dispatch an MCP tool call to the matching muxdev service."""
    if name == "provider.detect":
        return {"content": [{"type": "json", "json": [probe.to_dict() for probe in detect_providers()]}]}
    if name == "workspace.search":
        query = str(arguments["query"])
        limit = int(arguments.get("limit", 20))
        return {"content": [{"type": "json", "json": _search(workspace, query, limit)}]}
    if name == "rag.query":
        query = str(arguments["query"])
        limit = int(arguments.get("limit", 5))
        return {"content": [{"type": "json", "json": LocalRagIndex(workspace).query(query, limit=limit)}]}
    if name == "workflow.plugins":
        return {"content": [{"type": "json", "json": [plugin.to_dict() for plugin in list_workflow_plugins()]}]}
    if name == "workflow.render":
        return {
            "content": [
                {
                    "type": "json",
                    "json": render_plugin_command(
                        str(arguments["plugin"]),
                        str(arguments["phase"]),
                        str(arguments["provider"]),
                        str(arguments.get("task", "")),
                    ),
                }
            ]
        }
    if name == "flow.list":
        return {"content": [{"type": "json", "json": [flow.to_dict() for flow in FlowRegistry(workspace).list()]}]}
    raise ValueError(f"unknown tool: {name}")


def _search(workspace: Path, query: str, limit: int) -> list[dict[str, object]]:
    """Perform a small dependency-free text search for MCP clients."""
    runtime_root = str(load_config(workspace).get("paths", {}).get("runtime_root", ".muxdev"))
    ignored = {".git", runtime_root, ".muxdev", ".pytest_cache", "__pycache__"}
    rows: list[dict[str, object]] = []
    needle = query.lower()
    for path in workspace.rglob("*"):
        if len(rows) >= limit:
            break
        if not path.is_file() or any(part in ignored for part in path.relative_to(workspace).parts):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if needle in line.lower():
                rows.append({"path": path.relative_to(workspace).as_posix(), "line": number, "text": line.strip()})
                if len(rows) >= limit:
                    break
    return rows
