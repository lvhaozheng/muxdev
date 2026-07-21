"""Official-SDK-ready MCP control server backed by the existing TaskService."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from .. import __version__
from ..application import TaskService
from ..runtime import RunEngine
from ..services.evidence_verify import verify_evidence_report


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunInput(ToolInput):
    task: str = Field(min_length=1)
    provider: str = "mock"
    workflow: Literal["change", "design", "review", "test"] = "change"
    profile: Literal["lite", "standard", "strict"] = "standard"
    max_cost_usd: float = Field(default=0.5, gt=0)


class RunIdInput(ToolInput):
    run_id: str = Field(min_length=1)


class ResumeInput(RunIdInput):
    action: Literal["auto", "fix-output", "retry", "switch-provider"] = "auto"


class ListRunsInput(ToolInput):
    status: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)


class RespondInput(ToolInput):
    interaction_id: str = Field(min_length=1)
    status: Literal["approved", "rejected", "responded"]
    response: str | None = None


class RunHandle(BaseModel):
    run_id: str
    status: str
    evidence: str | None = None


class ObjectOutput(RootModel[dict[str, Any]]):
    pass


class ListOutput(BaseModel):
    runs: list[dict[str, Any]]


TOOLS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "muxdev_run": (RunInput, RunHandle),
    "muxdev_get_run": (RunIdInput, ObjectOutput),
    "muxdev_list_runs": (ListRunsInput, ListOutput),
    "muxdev_resume_run": (ResumeInput, RunHandle),
    "muxdev_cancel_run": (RunIdInput, RunHandle),
    "muxdev_respond": (RespondInput, ObjectOutput),
    "muxdev_get_evidence": (RunIdInput, ObjectOutput),
    "muxdev_verify_evidence": (RunIdInput, ObjectOutput),
}


@contextmanager
def _tasks(workspace: Path):
    engine = RunEngine(workspace)
    try:
        yield TaskService(engine, engine.store)
    finally:
        engine.store.close()


def server_manifest(workspace: Path | None = None) -> dict[str, Any]:
    del workspace
    return {
        "name": "muxdev",
        "version": __version__,
        "protocol": "mcp",
        "transport": "stdio",
        "tools": [
            {
                "name": name,
                "description": _description(name),
                "inputSchema": input_model.model_json_schema(),
                "outputSchema": output_model.model_json_schema(),
            }
            for name, (input_model, output_model) in TOOLS.items()
        ],
    }


def handle_jsonrpc(request: dict[str, Any] | str, *, workspace: Path | None = None) -> dict[str, Any]:
    """Compatibility shim for tests and older local integrations."""
    payload = json.loads(request) if isinstance(request, str) else request
    identifier = payload.get("id")
    method = str(payload.get("method") or "")
    try:
        if method == "initialize":
            result = {"serverInfo": {"name": "muxdev", "version": __version__}, "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": server_manifest(workspace)["tools"]}
        elif method == "tools/call":
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            result = _call(str(params.get("name") or ""), params.get("arguments") or {}, workspace or Path.cwd())
        else:
            raise ValueError(f"unsupported method: {method}")
        return {"jsonrpc": "2.0", "id": identifier, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32000, "message": str(exc)}}


def _call(name: str, arguments: dict[str, Any], workspace: Path) -> dict[str, Any]:
    handlers: dict[str, Callable[[dict[str, Any], Path], Any]] = {
        "muxdev_run": _run,
        "muxdev_get_run": _get_run,
        "muxdev_list_runs": _list_runs,
        "muxdev_resume_run": _resume,
        "muxdev_cancel_run": _cancel,
        "muxdev_respond": _respond,
        "muxdev_get_evidence": _get_evidence,
        "muxdev_verify_evidence": _verify_evidence,
    }
    if name not in handlers:
        raise ValueError(f"unknown muxdev tool: {name}")
    input_model, output_model = TOOLS[name]
    validated = input_model.model_validate(arguments).model_dump(mode="python")
    value = handlers[name](validated, workspace.resolve())
    normalized = output_model.model_validate(value).model_dump(mode="json")
    if isinstance(normalized, dict) and "root" in normalized:
        normalized = normalized["root"]
    return {
        "content": [{"type": "text", "text": json.dumps(normalized, ensure_ascii=False, default=str)}],
        "structuredContent": normalized,
    }


def serve_stdio(workspace: Path) -> None:
    """Run the official MCP Python SDK over stdio; dependency is optional."""
    try:
        import mcp.server.lowlevel  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("MCP support requires: pip install 'muxdev[interop]'") from exc
    asyncio.run(_serve_stdio(workspace.resolve()))


async def _serve_stdio(workspace: Path) -> None:
    from mcp import types
    from mcp.server.lowlevel import NotificationOptions, Server
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server

    server = Server("muxdev", version=__version__)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                description=_description(name),
                inputSchema=input_model.model_json_schema(),
                outputSchema=output_model.model_json_schema(),
            )
            for name, (input_model, output_model) in TOOLS.items()
        ]

    @server.call_tool(validate_input=True)
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = _call(name, arguments, workspace)["structuredContent"]
        if not isinstance(result, dict):
            raise TypeError(f"MCP structured output must be an object: {name}")
        return result

    options = InitializationOptions(
        server_name="muxdev",
        server_version=__version__,
        capabilities=server.get_capabilities(NotificationOptions(), {}),
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options)


def _run(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        result = tasks.create(
            str(args["task"]),
            provider=args["provider"],
            workflow_name=str(args["workflow"]),
            profile=str(args["profile"]),
            max_cost_usd=float(args["max_cost_usd"]),
        )
        return {"run_id": result.run_id, "status": str(result.status), "evidence": str(result.report_path)}


def _get_run(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        return tasks.get(str(args["run_id"]))


def _list_runs(args: dict[str, Any], workspace: Path) -> dict[str, list[dict[str, Any]]]:
    with _tasks(workspace) as tasks:
        return {"runs": tasks.list(status=args["status"], limit=int(args["limit"]))}


def _resume(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        result = tasks.resume(str(args["run_id"]), action=str(args["action"]))
        return {"run_id": result.run_id, "status": str(result.status), "evidence": str(result.report_path)}


def _cancel(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        tasks.cancel(str(args["run_id"]))
        return {"run_id": str(args["run_id"]), "status": "aborted", "evidence": None}


def _respond(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        return tasks.respond(
            str(args["interaction_id"]), status=str(args["status"]), response=args["response"]
        )


def _get_evidence(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        path = _report_path(tasks, str(args["run_id"]), workspace)
        return json.loads(path.read_text(encoding="utf-8"))


def _verify_evidence(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        path = _report_path(tasks, str(args["run_id"]), workspace)
        return verify_evidence_report(path, store=tasks.store)


def _report_path(tasks: TaskService, run_id: str, workspace: Path) -> Path:
    state = tasks.get(run_id)
    run = state["run"]
    metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    return Path(str(metadata.get("run_dir") or workspace / ".muxdev" / "runs" / run_id)) / "evidence-report.json"


def _description(name: str) -> str:
    return {
        "muxdev_run": "Start one trusted-delivery run.",
        "muxdev_get_run": "Get run, stage, and interaction state.",
        "muxdev_list_runs": "List runs.",
        "muxdev_resume_run": "Resume a durable run.",
        "muxdev_cancel_run": "Cancel a run and clean its process tree.",
        "muxdev_respond": "Respond to a pending human interaction.",
        "muxdev_get_evidence": "Get the canonical Evidence v3 report.",
        "muxdev_verify_evidence": "Verify evidence, artifacts, gate, and event chain.",
    }[name]
