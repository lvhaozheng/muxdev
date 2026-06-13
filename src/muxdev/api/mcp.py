"""Minimal JSON-RPC/MCP-compatible control surface.

The MCP server exposes read-oriented muxdev capabilities to external agents:
    provider discovery, workspace search, local RAG, workflow templates, command
rendering, and flow listing. Tool handlers reuse the same services as the CLI so
there is one behavior source for humans, scripts, and agent clients.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import __version__
from ..config.loader import load_config
from ..config.runtime import load_runtime_config
from ..core.safety import SafetyPolicyEngine
from ..providers import detect_providers
from ..presentation.dashboard import build_dashboard_overview
from ..services.evidence import verify_run_evidence
from ..services.flows import FlowRegistry
from ..services.rag import LocalRagIndex
from ..services.ux import build_provider_health
from ..services.workflow_plugins import list_workflow_plugins, render_plugin_command
from ..storage import Blackboard, MemoryStore, RunStore, canonical_hash, sha256_text


def server_manifest(workspace: Path | None = None) -> dict[str, Any]:
    """Return the tool manifest advertised to MCP clients."""
    resources = resource_manifest()
    prompts = prompt_manifest()
    data = {
        "name": "muxdev",
        "version": __version__,
        "protocol": "jsonrpc-2.0",
        "mode": _mcp_mode(workspace) if workspace is not None else "local_stdio",
        "write_policy": _mcp_write_policy(workspace) if workspace is not None else "guarded",
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
                "name": "workflow.templates",
                "description": "List muxdev workflow template definitions.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "workflow.plugins",
                "description": "Deprecated alias for workflow.templates.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "workflow.render",
                "description": "Render a workflow template phase command for a provider.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "template": {"type": "string"},
                        "plugin": {"type": "string", "description": "Deprecated alias for template."},
                        "phase": {"type": "string"},
                        "provider": {"type": "string"},
                        "task": {"type": "string"},
                    },
                    "required": ["phase", "provider"],
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
                "description": "Query explicit project memory.",
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
            {
                "name": "muxdev.submit_task",
                "description": "Submit a muxdev task when explicitly enabled in config.",
                "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}, "provider": {"type": "string"}, "workflow": {"type": "string"}}},
            },
        ],
        "resources": resources,
        "prompts": prompts,
    }
    if workspace is not None:
        if not _mcp_enabled(workspace):
            data["tools"] = []
            data["resources"] = []
            data["prompts"] = []
            return data
        data["tools"] = _filter_tools(data["tools"], workspace)
        data["resources"] = _filter_resources(data["resources"], workspace)
    return data


def resource_manifest() -> list[dict[str, Any]]:
    """Return lightweight muxdev MCP resource definitions."""
    return [
        {"uri": "muxdev://workspace/summary", "name": "Workspace Summary", "mimeType": "application/json"},
        {"uri": "muxdev://dashboard/overview", "name": "Dashboard Overview", "mimeType": "application/json"},
        {"uri": "muxdev://runs/{run_id}", "name": "Run Detail", "mimeType": "application/json"},
        {"uri": "muxdev://runs/{run_id}/artifacts", "name": "Run Artifacts", "mimeType": "application/json"},
        {"uri": "muxdev://memory", "name": "Project Memory", "mimeType": "application/json"},
    ]


def prompt_manifest() -> list[dict[str, Any]]:
    """Return reusable prompt definitions for external MCP clients."""
    return [
        {"name": "muxdev.workflow.design", "description": "Design a muxdev implementation plan.", "arguments": [{"name": "task", "required": True}]},
        {"name": "muxdev.workflow.review", "description": "Review a muxdev change with evidence.", "arguments": [{"name": "run_id", "required": False}]},
        {"name": "muxdev.skill.explain", "description": "Explain which skills fit a task.", "arguments": [{"name": "task", "required": True}]},
        {"name": "muxdev.acceptance.review", "description": "Check acceptance criteria and delivery evidence.", "arguments": [{"name": "run_id", "required": True}]},
    ]


def mcp_summary(workspace: Path, *, ecosystem: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the compact dashboard-facing MCP status payload."""
    manifest = server_manifest(workspace)
    guardrails = _recent_guardrails(ecosystem)
    return {
        "status": "enabled" if _mcp_enabled(workspace) else "disabled",
        "mode": _mcp_mode(workspace).replace("_", " "),
        "tools_count": len(manifest.get("tools", [])),
        "resources_count": len(manifest.get("resources", [])),
        "prompts_count": len(manifest.get("prompts", [])),
        "write_policy": _mcp_write_policy(workspace),
        "recent_guardrails": guardrails,
        "recent_denials": sum(1 for row in guardrails if str(row.get("decision")) == "deny"),
        "commands": {
            "manifest": "muxdev mcp manifest --json",
            "doctor": "muxdev mcp doctor --json",
            "serve": "muxdev mcp serve --stdio",
        },
    }


def mcp_doctor(workspace: Path) -> dict[str, Any]:
    """Return a CLI-friendly health summary for the local muxdev MCP surface."""
    summary = mcp_summary(workspace)
    return {
        **summary,
        "server": "muxdev",
        "protocol": "jsonrpc-2.0",
        "read_only_default": True,
        "allow_submit_task": _mcp_allow_submit_task(workspace),
        "resources": resource_manifest(),
        "prompts": prompt_manifest(),
    }


def handle_jsonrpc(request: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Handle one JSON-RPC request and wrap exceptions as protocol errors."""
    method = request.get("method")
    request_id = request.get("id")
    try:
        if method == "initialize":
            result = {"serverInfo": {"name": "muxdev", "version": __version__}, "capabilities": {"tools": {}, "resources": {}, "prompts": {}}}
        elif method == "tools/list":
            result = {"tools": server_manifest(workspace)["tools"]}
        elif method == "tools/call":
            params = request.get("params") or {}
            result = _call_tool(str(params.get("name")), params.get("arguments") or {}, workspace)
        elif method == "resources/list":
            result = {"resources": server_manifest(workspace)["resources"]}
        elif method == "resources/read":
            params = request.get("params") or {}
            result = _read_resource(str(params.get("uri") or ""), workspace)
        elif method == "prompts/list":
            result = {"prompts": server_manifest(workspace)["prompts"]}
        elif method == "prompts/get":
            params = request.get("params") or {}
            result = _get_prompt(str(params.get("name") or ""), params.get("arguments") or {}, workspace)
        else:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}


def _call_tool(name: str, arguments: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Dispatch an MCP tool call to the matching muxdev service."""
    if not _mcp_enabled(workspace):
        raise ValueError("MCP is disabled; set mcp.enabled = true to enable the local server surface")
    if not _tool_allowed(name, workspace):
        raise ValueError(f"tool is not allowed by mcp.allowed_tools: {name}")
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
    if name in {"workflow.templates", "workflow.plugins"}:
        return {"content": [{"type": "json", "json": [plugin.to_dict() for plugin in list_workflow_plugins()]}]}
    if name == "workflow.render":
        template = arguments.get("template", arguments.get("plugin"))
        if template is None:
            raise ValueError("workflow.render requires template")
        return {
            "content": [
                {
                    "type": "json",
                    "json": render_plugin_command(
                        str(template),
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
    if name == "muxdev.submit_task":
        if not _mcp_allow_submit_task(workspace):
            raise ValueError("muxdev.submit_task is disabled; set mcp.allow_submit_task = true to enable guarded task submission")
        raise ValueError("muxdev.submit_task is not implemented in this MCP server version")
    raise ValueError(f"unknown tool: {name}")


def _read_resource(uri: str, workspace: Path) -> dict[str, Any]:
    if not _mcp_enabled(workspace):
        raise ValueError("MCP is disabled; set mcp.enabled = true to enable resources")
    if not _resource_allowed(uri, workspace):
        raise ValueError(f"resource is not allowed by mcp.allowed_resources: {uri}")
    if uri == "muxdev://workspace/summary":
        return _resource_json(uri, {"workspace": str(workspace), "config": _mcp_config(workspace)})
    if uri == "muxdev://dashboard/overview":
        with _blackboard_for(workspace, None)[0] as board:
            ecosystem = {name: board.table_rows(name) for name in ("guardrail_events", "feedback_events", "ci_rescues", "cache_entries", "skill_locks")}
        overview = build_dashboard_overview(
            workspace,
            daemon={"status": "mcp"},
            tasks=[],
            approvals=[],
            provider_actions=[],
            provider_health=build_provider_health([probe.to_dict() for probe in detect_providers()]),
            ecosystem=ecosystem,
        )
        return _resource_json(uri, overview)
    if uri == "muxdev://memory":
        with MemoryStore(workspace) as store:
            return _resource_json(uri, store.query("", limit=20))
    if uri.startswith("muxdev://runs/"):
        parts = uri.removeprefix("muxdev://runs/").split("/")
        run_id = parts[0]
        run_dir = RunStore(workspace).find_run_dir(run_id)
        with Blackboard(run_dir) as board:
            if len(parts) > 1 and parts[1] == "artifacts":
                return _resource_json(uri, board.table_rows("artifacts", run_id=run_id))
            return _resource_json(uri, {"run": board.get_run(run_id), "stages": board.table_rows("stages", run_id=run_id)})
    raise ValueError(f"unknown resource: {uri}")


def _get_prompt(name: str, arguments: dict[str, Any], workspace: Path) -> dict[str, Any]:
    if not _mcp_enabled(workspace):
        raise ValueError("MCP is disabled; set mcp.enabled = true to enable prompts")
    task = str(arguments.get("task") or "")
    run_id = str(arguments.get("run_id") or "")
    prompts = {
        "muxdev.workflow.design": f"Design an implementation plan for this muxdev task:\n\n{task}",
        "muxdev.workflow.review": f"Review muxdev run {run_id or '<run_id>'}. Focus on evidence, tests, risks, and unresolved approvals.",
        "muxdev.skill.explain": f"Explain which muxdev skills should be activated for this task:\n\n{task}",
        "muxdev.acceptance.review": f"Review acceptance criteria and delivery evidence for muxdev run {run_id or '<run_id>'}.",
    }
    if name not in prompts:
        raise ValueError(f"unknown prompt: {name}")
    return {"description": name, "messages": [{"role": "user", "content": {"type": "text", "text": prompts[name]}}]}


def _resource_json(uri: str, payload: Any) -> dict[str, Any]:
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


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


def _mcp_config(workspace: Path) -> dict[str, Any]:
    try:
        config = load_runtime_config(workspace).get("mcp", {})
    except Exception:
        config = {}
    return config if isinstance(config, dict) else {}


def _filter_tools(tools: list[dict[str, Any]], workspace: Path) -> list[dict[str, Any]]:
    allowed = _allowed_values(workspace, "allowed_tools")
    if not allowed:
        return tools
    return [tool for tool in tools if str(tool.get("name")) in allowed]


def _filter_resources(resources: list[dict[str, Any]], workspace: Path) -> list[dict[str, Any]]:
    allowed = _allowed_values(workspace, "allowed_resources")
    if not allowed:
        return resources
    return [resource for resource in resources if str(resource.get("uri")) in allowed]


def _tool_allowed(name: str, workspace: Path) -> bool:
    allowed = _allowed_values(workspace, "allowed_tools")
    return not allowed or name in allowed


def _resource_allowed(uri: str, workspace: Path) -> bool:
    allowed = _allowed_values(workspace, "allowed_resources")
    return not allowed or uri in allowed or any(uri.startswith(value.split("{", 1)[0]) for value in allowed if "{" in value)


def _allowed_values(workspace: Path, key: str) -> set[str]:
    raw = _mcp_config(workspace).get(key, [])
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(item) for item in raw if str(item)}
    return set()


def _mcp_allow_submit_task(workspace: Path) -> bool:
    return bool(_mcp_config(workspace).get("allow_submit_task", False))


def _mcp_enabled(workspace: Path) -> bool:
    return bool(_mcp_config(workspace).get("enabled", True))


def _mcp_mode(workspace: Path) -> str:
    value = str(_mcp_config(workspace).get("mode") or "local_stdio")
    return value or "local_stdio"


def _mcp_write_policy(workspace: Path) -> str:
    config = _mcp_config(workspace)
    value = str(config.get("write_policy") or "")
    if value:
        return value
    return "guarded"


def _recent_guardrails(ecosystem: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = ecosystem.get("guardrail_events", []) if isinstance(ecosystem, dict) else []
    result = []
    for row in rows[-3:] if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        result.append(
            {
                "tool": row.get("tool"),
                "decision": row.get("decision"),
                "reason": row.get("reason"),
                "created_at": row.get("created_at"),
            }
        )
    return result


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
