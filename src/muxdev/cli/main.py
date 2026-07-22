"""The intentionally small muxdev command surface (30 leaf commands)."""

from __future__ import annotations

import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from ..api import create_app, serve_stdio
from ..application import TaskService
from ..config.loader import load_config
from ..providers import certify_provider, detect_providers, probe_provider
from ..runtime import ConversationService, RunEngine
from ..runtime.collaboration_service import CollaborationService
from ..services.agents import AgentRegistry
from ..services.dsse import export_dsse
from ..services.evidence_verify import verify_evidence_report
from ..services.router import ProviderRouter
from ..services.skills import (
    scan_skills,
    skill_show,
    validate_skill_path,
    verify_skill_bindings,
    verify_skill_lock,
    write_skill_lock,
)
from ..storage import ControlStore, compact_database_status, migrate_workspace


app = typer.Typer(no_args_is_help=True, help="Trusted delivery control plane for coding agents.")
evidence_app = typer.Typer(no_args_is_help=True)
route_app = typer.Typer(no_args_is_help=True)
provider_app = typer.Typer(no_args_is_help=True)
skill_app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
migrate_app = typer.Typer(no_args_is_help=True)
mcp_app = typer.Typer(no_args_is_help=True)
agent_app = typer.Typer(no_args_is_help=True)
collab_app = typer.Typer(no_args_is_help=True)
collab_plan_app = typer.Typer(no_args_is_help=True)
app.add_typer(evidence_app, name="evidence")
app.add_typer(route_app, name="route")
app.add_typer(provider_app, name="provider")
app.add_typer(skill_app, name="skill")
app.add_typer(config_app, name="config")
app.add_typer(migrate_app, name="migrate")
app.add_typer(mcp_app, name="mcp")
app.add_typer(agent_app, name="agent")
app.add_typer(collab_app, name="collab")
collab_app.add_typer(collab_plan_app, name="plan")
console = Console()
Workspace = Annotated[Path, typer.Option("--workspace", "-w", resolve_path=True)]


def _print(value: object, as_json: bool = False) -> None:
    if as_json or isinstance(value, (dict, list)):
        console.print_json(json.dumps(value, ensure_ascii=False, default=str))
    else:
        console.print(value)


def _store(workspace: Path) -> ControlStore:
    return ControlStore(workspace)


@contextmanager
def _tasks(workspace: Path):
    engine = RunEngine(workspace)
    try:
        yield TaskService(engine, engine.store)
    finally:
        engine.store.close()


@contextmanager
def _collaboration(workspace: Path):
    engine = RunEngine(workspace)
    try:
        yield CollaborationService(ConversationService(engine, engine.store), engine.store)
    finally:
        engine.store.close()


def _collab_workspace(workspace: Path) -> Path:
    injected = os.environ.get("MUXDEV_WORKSPACE")
    return Path(injected).resolve() if injected and workspace == Path.cwd() else workspace.resolve()


def _collab_identity(service: CollaborationService) -> dict[str, object]:
    token = os.environ.get("MUXDEV_CONTROL_TOKEN", "")
    if not token:
        raise typer.BadParameter("this command requires a task-scoped MUXDEV_CONTROL_TOKEN")
    expected = os.environ.get("MUXDEV_ASSIGNMENT_ID")
    try:
        return service.authorize_scoped_token(token, assignment_id=expected)
    except PermissionError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _load_json_file(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise typer.BadParameter("JSON input must contain an object")
    return value


def _run_report(workspace: Path, run_id: str) -> Path:
    with _store(workspace) as store:
        run = store.get_run(run_id)
    if not run:
        raise typer.BadParameter(f"run not found: {run_id}")
    metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    return Path(str(metadata.get("run_dir") or workspace / ".muxdev" / "runs" / run_id)) / "evidence-report.json"


@app.command("init")
def init(workspace: Workspace = Path.cwd()) -> None:
    with _store(workspace) as store:
        _print({"workspace": str(store.workspace), "database": str(store.path), "tables": list(store.table_names())})


@app.command("run")
def run(
    task: Annotated[str, typer.Argument(help="Task to execute")],
    workspace: Workspace = Path.cwd(),
    workflow: Annotated[str, typer.Option("--workflow")] = "change",
    profile: Annotated[str, typer.Option("--profile")] = "standard",
    provider: Annotated[str, typer.Option("--provider")] = "mock",
    max_cost: Annotated[float, typer.Option("--max-cost")] = 0.5,
) -> None:
    with _tasks(workspace) as tasks:
        result = tasks.create(
            task, provider=provider, workflow_name=workflow, profile=profile, max_cost_usd=max_cost
        )
    _print({"run_id": result.run_id, "status": str(result.status), "evidence": str(result.report_path)})


@app.command("list")
def list_runs(workspace: Workspace = Path.cwd(), status: str | None = None, limit: int = 100) -> None:
    with _tasks(workspace) as tasks:
        _print(tasks.list(status=status, limit=limit))


@app.command("show")
def show(run_id: str, workspace: Workspace = Path.cwd()) -> None:
    with _tasks(workspace) as tasks:
        try:
            _print(tasks.get(run_id))
        except FileNotFoundError as exc:
            raise typer.BadParameter(f"run not found: {run_id}") from exc


@app.command("resume")
def resume(
    run_id: str,
    workspace: Workspace = Path.cwd(),
    action: Annotated[str, typer.Option("--action")] = "auto",
) -> None:
    if action not in {"auto", "fix-output", "retry", "switch-provider"}:
        raise typer.BadParameter("action must be auto, fix-output, retry, or switch-provider")
    with _tasks(workspace) as tasks:
        result = tasks.resume(run_id, action=action)
    _print({"run_id": result.run_id, "status": str(result.status), "evidence": str(result.report_path)})


@app.command("cancel")
def cancel(run_id: str, workspace: Workspace = Path.cwd()) -> None:
    with _tasks(workspace) as tasks:
        tasks.cancel(run_id)
    _print({"run_id": run_id, "status": "aborted"})


def _respond(interaction_id: str, status: str, workspace: Path, message: str | None = None) -> None:
    with _tasks(workspace) as tasks:
        _print(tasks.respond(interaction_id, status=status, response=message))


@app.command("approve")
def approve(interaction_id: str, workspace: Workspace = Path.cwd(), message: str | None = None) -> None:
    _respond(interaction_id, "approved", workspace, message)


@app.command("reject")
def reject(interaction_id: str, workspace: Workspace = Path.cwd(), message: str | None = None) -> None:
    _respond(interaction_id, "rejected", workspace, message)


@app.command("respond")
def respond(interaction_id: str, message: str, workspace: Workspace = Path.cwd()) -> None:
    _respond(interaction_id, "responded", workspace, message)


@evidence_app.command("show")
def evidence_show(run_id: str, workspace: Workspace = Path.cwd()) -> None:
    _print(json.loads(_run_report(workspace, run_id).read_text(encoding="utf-8")))


@evidence_app.command("verify")
def evidence_verify(run_id: str, workspace: Workspace = Path.cwd()) -> None:
    with _store(workspace) as store:
        result = verify_evidence_report(_run_report(workspace, run_id), store=store)
    _print(result)
    if not result["valid"]:
        raise typer.Exit(1)


@evidence_app.command("export")
def evidence_export(
    run_id: str,
    workspace: Workspace = Path.cwd(),
    key: Annotated[Path | None, typer.Option("--key", resolve_path=True)] = None,
    output: Annotated[Path | None, typer.Option("--output", resolve_path=True)] = None,
) -> None:
    result = export_dsse(_run_report(workspace, run_id), private_key=key, output=output)
    with _store(workspace) as store:
        path = Path(str(result["path"]))
        digest = "sha256:" + __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        store.add_attestation(run_id, kind="dsse", path=path, digest=digest, payload={"signed": result["signed"]})
    _print({"path": result["path"], "signed": result["signed"]})


@route_app.command("explain")
def route_explain(run_id: str, workspace: Workspace = Path.cwd()) -> None:
    with _store(workspace) as store:
        _print(ProviderRouter(store).explain(run_id))


@route_app.command("replay")
def route_replay(run_id: str, workspace: Workspace = Path.cwd()) -> None:
    with _store(workspace) as store:
        _print(ProviderRouter(store).replay(run_id))


@route_app.command("benchmark")
def route_benchmark(workspace: Workspace = Path.cwd()) -> None:
    with _store(workspace) as store:
        _print(ProviderRouter(store).benchmark())


@provider_app.command("list")
def provider_list(workspace: Workspace = Path.cwd()) -> None:
    _print([item.to_dict() for item in detect_providers(workspace=workspace)])


@provider_app.command("show")
def provider_show(name: str, workspace: Workspace = Path.cwd()) -> None:
    _print(probe_provider(name, workspace=workspace).to_dict())


@provider_app.command("doctor")
def provider_doctor(name: str | None = None, workspace: Workspace = Path.cwd()) -> None:
    probes = [probe_provider(name, workspace=workspace)] if name else detect_providers(workspace=workspace)
    _print({"healthy": all(item.installed for item in probes), "providers": [item.to_dict() for item in probes]})


@provider_app.command("certify")
def provider_certify(name: str, workspace: Workspace = Path.cwd(), live: bool = False) -> None:
    payload = certify_provider(workspace, name, live=live)
    status = str(payload["status"])
    with _store(workspace) as store:
        certification_id = store.record_certification(name, status=status, payload=payload)
    _print({"certification_id": certification_id, **payload})
    if status == "failed":
        raise typer.Exit(1)


@agent_app.command("list")
def agent_list(workspace: Workspace = Path.cwd()) -> None:
    _print(AgentRegistry(workspace).list())


@agent_app.command("show")
def agent_show(agent_id: str, workspace: Workspace = Path.cwd()) -> None:
    registry = AgentRegistry(workspace)
    items = [item for item in registry.list() if item["agent_id"] == agent_id]
    if not items:
        raise typer.BadParameter(f"unknown agent: {agent_id}")
    _print(items[0])


@agent_app.command("doctor")
def agent_doctor(agent_id: str | None = None, workspace: Workspace = Path.cwd()) -> None:
    try:
        result = AgentRegistry(workspace).doctor(agent_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print(result)
    if not result["healthy"]:
        raise typer.Exit(1)


@collab_app.command("roster")
def collab_roster(workspace: Workspace = Path.cwd()) -> None:
    resolved = _collab_workspace(workspace)
    with _collaboration(resolved) as service:
        identity = _collab_identity(service)
        conversation_id = str(identity["conversation_id"])
        _print(
            {
                "conversation_id": conversation_id,
                "agents": service.registry.list(enabled_only=True),
                "participants": service.get(conversation_id)["participants"],
            }
        )


@collab_plan_app.command("propose")
def collab_plan_propose(
    file: Annotated[Path, typer.Option("--file", resolve_path=True)],
    workspace: Workspace = Path.cwd(),
) -> None:
    resolved = _collab_workspace(workspace)
    with _collaboration(resolved) as service:
        identity = _collab_identity(service)
        assignment = service.store.get_assignment(str(identity["assignment_id"])) or {}
        if assignment.get("dispatch_kind") != "orchestrate":
            raise typer.BadParameter("only the orchestrator assignment may propose a plan")
        _print(
            service.propose_plan(
                str(identity["conversation_id"]),
                _load_json_file(file),
                orchestrator_agent_id=str(identity["agent_id"]),
            )
        )


@collab_app.command("dispatch")
def collab_dispatch(
    file: Annotated[Path, typer.Option("--file", resolve_path=True)],
    workspace: Workspace = Path.cwd(),
) -> None:
    resolved = _collab_workspace(workspace)
    with _collaboration(resolved) as service:
        identity = _collab_identity(service)
        _print(
            service.dispatch(
                str(identity["conversation_id"]),
                _load_json_file(file),
                parent_assignment_id=str(identity["assignment_id"]),
            )
        )


@collab_app.command("send")
def collab_send(
    content: Annotated[str, typer.Argument(help="Message to send")],
    to: Annotated[str, typer.Option("--to")],
    workspace: Workspace = Path.cwd(),
) -> None:
    resolved = _collab_workspace(workspace)
    with _collaboration(resolved) as service:
        identity = _collab_identity(service)
        recipient = to
        target_assignment = service.store.get_assignment(to)
        if target_assignment:
            if str(target_assignment["conversation_id"]) != str(identity["conversation_id"]):
                raise typer.BadParameter("target assignment belongs to another conversation")
            recipient = str(target_assignment["agent_id"])
        _print(
            service.add_message(
                str(identity["conversation_id"]), content, recipients=[recipient]
            )
        )


@collab_app.command("ask")
def collab_ask(
    content: Annotated[str, typer.Argument(help="Question for another agent")],
    to: Annotated[str, typer.Option("--to")],
    workspace: Workspace = Path.cwd(),
) -> None:
    resolved = _collab_workspace(workspace)
    with _collaboration(resolved) as service:
        identity = _collab_identity(service)
        _print(
            service.add_message(
                str(identity["conversation_id"]),
                content,
                recipients=[to],
                dispatch_kind="consult",
            )
        )


@collab_app.command("report")
def collab_report(
    file: Annotated[Path, typer.Option("--file", resolve_path=True)],
    workspace: Workspace = Path.cwd(),
) -> None:
    resolved = _collab_workspace(workspace)
    with _collaboration(resolved) as service:
        identity = _collab_identity(service)
        _print(service.report(str(identity["assignment_id"]), _load_json_file(file)))


@collab_app.command("deliver")
def collab_deliver(
    manifest: Annotated[Path, typer.Option("--manifest", resolve_path=True)],
    workspace: Workspace = Path.cwd(),
) -> None:
    resolved = _collab_workspace(workspace)
    with _collaboration(resolved) as service:
        identity = _collab_identity(service)
        _print(service.report(str(identity["assignment_id"]), _load_json_file(manifest)))


@skill_app.command("list")
def skill_list(workspace: Workspace = Path.cwd()) -> None:
    _print([item.to_dict() for item in scan_skills(workspace, include_disabled=True)])


@skill_app.command("show")
def skill_get(name: str, workspace: Workspace = Path.cwd()) -> None:
    _print(skill_show(workspace, name))


@skill_app.command("verify")
def skill_verify(name: str, workspace: Workspace = Path.cwd()) -> None:
    item = skill_show(workspace, name)
    result = validate_skill_path(Path(str(item["path"])), strict=True)
    bindings = verify_skill_bindings(workspace, name)
    result["bindings"] = bindings["bindings"]
    result["errors"] = [*result["errors"], *bindings["errors"]]
    result["valid"] = not result["errors"]
    _print(result)
    if not result["valid"]:
        raise typer.Exit(1)


@skill_app.command("lock")
def skill_lock(workspace: Workspace = Path.cwd(), verify: bool = False) -> None:
    if verify:
        _print(verify_skill_lock(workspace))
        return
    result = write_skill_lock(workspace)
    with _store(workspace) as store:
        for skill in result["skills"]:
            hashes = skill.get("hashes") if isinstance(skill.get("hashes"), dict) else {}
            store.record_skill_lock(
                str(skill["name"]), str(skill.get("version") or "unversioned"), str(hashes.get("tree") or ""), skill
            )
    _print(result)


@config_app.command("show")
def config_show(workspace: Workspace = Path.cwd()) -> None:
    _print(load_config(workspace))


@config_app.command("validate")
def config_validate(workspace: Workspace = Path.cwd()) -> None:
    config = load_config(workspace)
    workflows = config.get("workflows", {})
    valid = isinstance(workflows, dict) and set(workflows) == {"change", "design", "review", "test"}
    result = {"valid": valid, "workflows": sorted(workflows) if isinstance(workflows, dict) else []}
    _print(result)
    if not valid:
        raise typer.Exit(1)


@migrate_app.command("status")
def migrate_status(workspace: Workspace = Path.cwd()) -> None:
    _print(compact_database_status(workspace))


@migrate_app.command("run")
def migrate_run(workspace: Workspace = Path.cwd()) -> None:
    _print(migrate_workspace(workspace))


@mcp_app.command("serve")
def mcp_serve(
    workspace: Workspace = Path.cwd(),
    transport: Annotated[str, typer.Option("--transport")] = "stdio",
) -> None:
    if transport != "stdio":
        raise typer.BadParameter("muxdev Trusted Harness v1 only supports MCP stdio")
    serve_stdio(workspace)


@app.command("doctor")
def doctor(workspace: Workspace = Path.cwd()) -> None:
    from muxdev.storage import CORE_TABLES

    with _store(workspace) as store:
        tables = store.table_names()
        result = {
            "healthy": set(tables) == set(CORE_TABLES),
            "database": str(store.path),
            "schema_version": 10,
            "tables": list(tables),
        }
    _print(result)
    if not result["healthy"]:
        raise typer.Exit(1)


@app.command("serve")
def serve(
    workspace: Workspace = Path.cwd(),
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_remote: Annotated[bool, typer.Option("--allow-remote")] = False,
    trusted_origin: Annotated[list[str] | None, typer.Option("--trusted-origin")] = None,
) -> None:
    import uvicorn

    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not loopback and not allow_remote:
        raise typer.BadParameter("non-loopback Web binding requires --allow-remote")
    pairing_code = secrets.token_urlsafe(8) if allow_remote else None
    if pairing_code:
        console.print("Remote Web access is protected. Pair a browser with this one-time code:")
        console.print(f"[bold]{pairing_code}[/bold]")
    uvicorn.run(
        create_app(
            workspace,
            require_auth=allow_remote,
            pairing_code=pairing_code,
            trusted_origins=tuple(trusted_origin or ()),
        ),
        host=host,
        port=port,
    )


if __name__ == "__main__":
    app()
