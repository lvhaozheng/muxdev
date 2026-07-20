"""MCP/JSON-RPC adapter exposing exactly eight trusted-delivery tools."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..application import TaskService
from ..runtime import RunEngine
from ..services.evidence_verify import verify_evidence_report
from ..storage import ControlStore


TOOLS = (
    "muxdev_run",
    "muxdev_get_run",
    "muxdev_list_runs",
    "muxdev_resume_run",
    "muxdev_cancel_run",
    "muxdev_respond",
    "muxdev_get_evidence",
    "muxdev_verify_evidence",
)


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
        "protocol": "jsonrpc-2.0",
        "tools": [{"name": name, "description": _description(name), "inputSchema": {"type": "object"}} for name in TOOLS],
    }


def handle_jsonrpc(request: dict[str, Any] | str, *, workspace: Path | None = None) -> dict[str, Any]:
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
    value = handlers[name](arguments, workspace.resolve())
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}], "structuredContent": value}


def _run(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        result = tasks.create(
            str(args["task"]), provider=args.get("provider", "mock"), workflow_name=str(args.get("workflow", "change")),
            profile=str(args.get("profile", "standard")), max_cost_usd=float(args.get("max_cost_usd", 0.5)),
        )
        return {"run_id": result.run_id, "status": str(result.status), "evidence": str(result.report_path)}


def _get_run(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        return tasks.get(str(args["run_id"]))


def _list_runs(args: dict[str, Any], workspace: Path) -> list[dict[str, Any]]:
    with _tasks(workspace) as tasks:
        return tasks.list(status=args.get("status"), limit=int(args.get("limit", 100)))


def _resume(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        result = tasks.resume(str(args["run_id"]))
        return {"run_id": result.run_id, "status": str(result.status), "evidence": str(result.report_path)}


def _cancel(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        tasks.cancel(str(args["run_id"]))
        return {"run_id": str(args["run_id"]), "status": "aborted"}


def _respond(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    with _tasks(workspace) as tasks:
        return tasks.respond(
            str(args["interaction_id"]), status=str(args["status"]), response=args.get("response")
        )


def _get_evidence(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    path, _ = _report(str(args["run_id"]), workspace)
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_evidence(args: dict[str, Any], workspace: Path) -> dict[str, Any]:
    path, store = _report(str(args["run_id"]), workspace)
    try:
        return verify_evidence_report(path, store=store)
    finally:
        store.close()


def _report(run_id: str, workspace: Path) -> tuple[Path, ControlStore]:
    store = ControlStore(workspace)
    run = store.get_run(run_id)
    if not run:
        store.close()
        raise FileNotFoundError(run_id)
    metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    return Path(str(metadata.get("run_dir") or workspace / ".muxdev" / "runs" / run_id)) / "evidence-report.json", store


def _description(name: str) -> str:
    return {
        "muxdev_run": "Start one trusted-delivery run.",
        "muxdev_get_run": "Get run, stage, and interaction state.",
        "muxdev_list_runs": "List runs.",
        "muxdev_resume_run": "Resume a durable run.",
        "muxdev_cancel_run": "Cancel a run.",
        "muxdev_respond": "Respond to a pending human interaction.",
        "muxdev_get_evidence": "Get the canonical Evidence v3 report.",
        "muxdev_verify_evidence": "Verify evidence, artifacts, gate, and event chain.",
    }[name]
