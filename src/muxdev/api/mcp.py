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
from ..core.safety import SafetyPolicyEngine
from ..providers import detect_providers
from ..services.evidence import verify_run_evidence
from ..services.flows import FlowRegistry
from ..services.rag import LocalRagIndex
from ..services.workflow_plugins import list_workflow_plugins, render_plugin_command
from ..storage import Blackboard, MemoryStore, RunStore, canonical_hash, sha256_text


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
            {
                "name": "muxdev.check_policy",
                "description": "Evaluate a command or action against muxdev safety policy.",
                "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            },
            {
                "name": "muxdev.ask_approval",
                "description": "Create or describe a muxdev approval request bound to a subject hash.",
                "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "type": {"type": "string"}, "reason": {"type": "string"}, "subject": {"type": "object"}}},
            },
            {
                "name": "muxdev.write_event",
                "description": "Write a guardrail event into the local muxdev blackboard.",
                "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "tool": {"type": "string"}, "decision": {"type": "string"}, "reason": {"type": "string"}, "payload": {"type": "object"}}},
            },
            {
                "name": "muxdev.register_artifact",
                "description": "Register a local artifact for a muxdev run.",
                "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "path": {"type": "string"}, "kind": {"type": "string"}, "name": {"type": "string"}}},
            },
            {
                "name": "muxdev.read_blackboard",
                "description": "Read a safe blackboard table for a run or workspace ecosystem.",
                "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "table": {"type": "string"}}},
            },
            {
                "name": "muxdev.query_memory",
                "description": "Query evidence-grounded project memory.",
                "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer"}}},
            },
            {
                "name": "muxdev.verify_patch",
                "description": "Verify trusted delivery evidence or hash a patch text.",
                "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "patch_text": {"type": "string"}}},
            },
            {
                "name": "muxdev.get_acceptance_criteria",
                "description": "Return task and acceptance context for a run.",
                "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}},
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
    if name == "muxdev.check_policy":
        result = SafetyPolicyEngine().evaluate_shell(str(arguments["command"]))
        payload = {"decision": str(result.decision), "reason": result.reason, "command": str(arguments["command"])}
        _write_guardrail(workspace, run_id=str(arguments.get("run_id") or "") or None, tool=name, decision=payload["decision"], reason=payload["reason"], payload=payload)
        return {"content": [{"type": "json", "json": payload}]}
    if name == "muxdev.ask_approval":
        run_id = str(arguments.get("run_id") or "")
        approval_type = str(arguments.get("type") or "guardrail")
        reason = str(arguments.get("reason") or "MCP guardrail approval requested")
        subject = arguments.get("subject") if isinstance(arguments.get("subject"), dict) else {}
        if run_id:
            run_dir = RunStore(workspace).find_run_dir(run_id)
            with Blackboard(run_dir) as board:
                approval_id = board.create_approval(run_id, None, approval_type, reason, subject=subject)
            payload = {"approval_id": approval_id, "run_id": run_id, "type": approval_type, "subject_hash": canonical_hash(subject or {})}
        else:
            payload = {"approval_id": None, "type": approval_type, "subject_hash": canonical_hash(subject or {}), "status": "needs_run_id"}
        return {"content": [{"type": "json", "json": payload}]}
    if name == "muxdev.write_event":
        payload = arguments.get("payload") if isinstance(arguments.get("payload"), dict) else {}
        event_id = _write_guardrail(
            workspace,
            run_id=str(arguments.get("run_id") or "") or None,
            tool=str(arguments.get("tool") or name),
            decision=str(arguments.get("decision") or "recorded"),
            reason=str(arguments.get("reason") or "MCP guardrail event"),
            payload=payload,
        )
        return {"content": [{"type": "json", "json": {"event_id": event_id}}]}
    if name == "muxdev.register_artifact":
        run_id = str(arguments["run_id"])
        run_dir = RunStore(workspace).find_run_dir(run_id)
        artifact_path = Path(str(arguments["path"]))
        if not artifact_path.is_absolute():
            artifact_path = (workspace / artifact_path).resolve()
        with Blackboard(run_dir) as board:
            board.add_artifact(run_id, None, str(arguments.get("name") or artifact_path.name), artifact_path, str(arguments.get("kind") or "mcp_artifact"))
        return {"content": [{"type": "json", "json": {"run_id": run_id, "path": str(artifact_path), "status": "registered"}}]}
    if name == "muxdev.read_blackboard":
        table = str(arguments.get("table") or "runs")
        run_id = str(arguments.get("run_id") or "")
        board, owns = _blackboard_for(workspace, run_id or None)
        try:
            rows = board.table_rows(table, run_id=run_id or None)
        finally:
            if owns:
                board.close()
        return {"content": [{"type": "json", "json": rows}]}
    if name == "muxdev.query_memory":
        with MemoryStore(workspace) as store:
            rows = store.query(str(arguments.get("query") or ""), status=str(arguments.get("status") or "active"), limit=int(arguments.get("limit", 8)))
        return {"content": [{"type": "json", "json": rows}]}
    if name == "muxdev.verify_patch":
        run_id = str(arguments.get("run_id") or "")
        if run_id:
            run_dir = RunStore(workspace).find_run_dir(run_id)
            with Blackboard(run_dir) as board:
                payload = verify_run_evidence(run_dir, run_id, board)
        else:
            patch_text = str(arguments.get("patch_text") or "")
            payload = {"patch_hash": sha256_text(patch_text), "valid": bool(patch_text)}
        return {"content": [{"type": "json", "json": payload}]}
    if name == "muxdev.get_acceptance_criteria":
        run_id = str(arguments["run_id"])
        run_dir = RunStore(workspace).find_run_dir(run_id)
        task = (run_dir / "task.md").read_text(encoding="utf-8", errors="replace") if (run_dir / "task.md").exists() else ""
        context = (run_dir / "task_context.json").read_text(encoding="utf-8", errors="replace") if (run_dir / "task_context.json").exists() else "{}"
        return {"content": [{"type": "json", "json": {"run_id": run_id, "task": task, "context": context}}]}
    raise ValueError(f"unknown tool: {name}")


def _blackboard_for(workspace: Path, run_id: str | None) -> tuple[Blackboard, bool]:
    if run_id:
        return Blackboard(RunStore(workspace).find_run_dir(run_id)), True
    root = workspace / ".muxdev"
    return Blackboard(root, db_path=root / "ecosystem.sqlite"), True


def _write_guardrail(workspace: Path, *, run_id: str | None, tool: str, decision: str, reason: str, payload: dict[str, Any]) -> str:
    board, owns = _blackboard_for(workspace, run_id)
    try:
        return board.add_guardrail_event(run_id=run_id, tool=tool, decision=decision, reason=reason, payload=payload)
    finally:
        if owns:
            board.close()


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
