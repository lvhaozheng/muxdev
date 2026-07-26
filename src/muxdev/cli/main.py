"""The intentionally small muxdev command surface (30 leaf commands)."""

from __future__ import annotations

import json
import os
import secrets
import socket
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

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
from ..services.skills.product import load_product_skill
from ..storage import (
    SCHEMA_VERSION,
    ControlStore,
    compact_database_status,
    migrate_workspace,
)
from ..workbench import WorkbenchRegistry, daemon_state_path, utc_now


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
project_app = typer.Typer(no_args_is_help=True)
app.add_typer(evidence_app, name="evidence")
app.add_typer(route_app, name="route")
app.add_typer(provider_app, name="provider")
app.add_typer(skill_app, name="skill")
app.add_typer(config_app, name="config")
app.add_typer(migrate_app, name="migrate")
app.add_typer(mcp_app, name="mcp")
app.add_typer(agent_app, name="agent")
app.add_typer(collab_app, name="collab")
app.add_typer(project_app, name="project")
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


def _identity_assignment(identity: dict[str, object]) -> str:
    return str(identity.get("current_assignment_id") or identity.get("assignment_id") or "")


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


def _daemon_state() -> dict[str, object] | None:
    path = daemon_state_path()
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _daemon_base_url(state: dict[str, object]) -> str:
    host = str(state.get("host") or "127.0.0.1")
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{display_host}:{int(state.get('port') or 8765)}"


def _daemon_health(state: dict[str, object]) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(
            _daemon_base_url(state) + "/api/v2/workbench/health",
            timeout=0.75,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if not isinstance(payload, dict) or payload.get("service") != "muxdev-workbench":
        return None
    expected = str(state.get("instance_id") or "")
    if expected and str(payload.get("instance_id") or "") != expected:
        return None
    return payload


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _port_is_open(host: str, port: int) -> bool:
    target = "127.0.0.1" if host in {"0.0.0.0", "::", "localhost"} else host
    try:
        with socket.create_connection((target, port), timeout=0.5):
            return True
    except OSError:
        return False


def _write_daemon_state(value: dict[str, object]) -> None:
    target = daemon_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, target)


def _clear_daemon_state(instance_id: str | None = None) -> None:
    target = daemon_state_path()
    current = _daemon_state()
    if instance_id and current and str(current.get("instance_id") or "") != instance_id:
        return
    target.unlink(missing_ok=True)


def _project_url(project_id: str, state: dict[str, object]) -> str:
    return f"{_daemon_base_url(state)}/projects/{project_id}"


def _print_project_url(project_id: str, state: dict[str, object]) -> None:
    url = _project_url(project_id, state)
    console.print(f"[bold]Muxdev Workbench[/bold]  [link={url}]{url}[/link]")


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
        assignment = service.store.get_assignment(_identity_assignment(identity)) or {}
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
                parent_assignment_id=_identity_assignment(identity) or None,
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
        assignment_id = _identity_assignment(identity)
        if not assignment_id:
            raise typer.BadParameter("the main Session has no active Assignment")
        _print(service.report(assignment_id, _load_json_file(file)))


@collab_app.command("deliver")
def collab_deliver(
    manifest: Annotated[Path, typer.Option("--manifest", resolve_path=True)],
    workspace: Workspace = Path.cwd(),
) -> None:
    resolved = _collab_workspace(workspace)
    with _collaboration(resolved) as service:
        identity = _collab_identity(service)
        assignment_id = _identity_assignment(identity)
        if not assignment_id:
            raise typer.BadParameter("the main Session has no active Assignment")
        _print(service.report(assignment_id, _load_json_file(manifest)))


@collab_app.command("clarify")
def collab_clarify(
    file: Annotated[Path, typer.Option("--file", resolve_path=True)],
    workspace: Workspace = Path.cwd(),
) -> None:
    resolved = _collab_workspace(workspace)
    with _collaboration(resolved) as service:
        identity = _collab_identity(service)
        assessment = _load_json_file(file)
        assignment_id = _identity_assignment(identity)
        if assignment_id:
            _print(service.request_assignment_input(assignment_id, assessment))
        else:
            _print(service.clarify(str(identity["conversation_id"]), assessment))


@collab_app.command("ready")
def collab_ready(
    file: Annotated[Path, typer.Option("--file", resolve_path=True)],
    workspace: Workspace = Path.cwd(),
) -> None:
    resolved = _collab_workspace(workspace)
    with _collaboration(resolved) as service:
        identity = _collab_identity(service)
        conversation = service.store.get_conversation(str(identity["conversation_id"])) or {}
        if str(identity.get("agent_id") or "") != str(conversation.get("primary_agent_id") or ""):
            raise typer.BadParameter("only the primary Agent may freeze requirements")
        _print(service.ready(str(identity["conversation_id"]), _load_json_file(file)))


@skill_app.command("list")
def skill_list(workspace: Workspace = Path.cwd()) -> None:
    _print([item.to_dict() for item in scan_skills(workspace, include_disabled=True)])


@skill_app.command("show")
def skill_get(name: str, workspace: Workspace = Path.cwd()) -> None:
    _print(skill_show(workspace, name))


@skill_app.command("load")
def skill_load(
    name: str,
    file: Annotated[str, typer.Option("--file")] = "SKILL.md",
    workspace: Workspace = Path.cwd(),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    resolved = Path(os.environ.get("MUXDEV_WORKSPACE") or workspace).resolve()
    try:
        result = load_product_skill(
            resolved,
            name,
            control_token=os.environ.get("MUXDEV_CONTROL_TOKEN", ""),
            relative_file=file,
            activation="auto",
        )
    except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if as_json:
        _print(result, as_json=True)
    else:
        typer.echo(str(result["content"]), nl=False)


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
            "schema_version": SCHEMA_VERSION,
            "tables": list(tables),
        }
    _print(result)
    if not result["healthy"]:
        raise typer.Exit(1)


@project_app.command("add")
def project_add(
    path: Annotated[Path, typer.Argument(resolve_path=True)] = Path.cwd(),
    name: Annotated[str | None, typer.Option("--name")] = None,
) -> None:
    registry = WorkbenchRegistry.user()
    try:
        _print(registry.register(path, name=name))
    finally:
        registry.store.close()


@project_app.command("list")
def project_list() -> None:
    registry = WorkbenchRegistry.user()
    try:
        _print(registry.list_projects())
    finally:
        registry.store.close()


@project_app.command("remove")
def project_remove(project_id: str) -> None:
    registry = WorkbenchRegistry.user()
    try:
        project = registry.store.get_project(project_id)
        if not project:
            raise typer.BadParameter(f"project not found: {project_id}")
        path = Path(str(project["path"]))
        if path.is_dir():
            with ControlStore(path) as store:
                active = [
                    session
                    for conversation in store.list_conversations(limit=1000)
                    for session in store.list_agent_sessions(
                        str(conversation["conversation_id"])
                    )
                    if session.get("status") not in {"closed", "failed"}
                ]
            if active:
                raise typer.BadParameter(
                    "project has active Agent Sessions; stop them before removing it"
                )
        removed = registry.store.remove_project(project_id)
        _print(
            {
                "project_id": project_id,
                "status": "removed",
                "path": removed["path"],
                "data_deleted": False,
            }
        )
    finally:
        registry.store.close()


@project_app.command("open")
def project_open(project_id: str) -> None:
    registry = WorkbenchRegistry.user()
    try:
        if not registry.store.get_project(project_id):
            raise typer.BadParameter(f"project not found: {project_id}")
        state = _daemon_state()
        if not state or not _daemon_health(state):
            raise typer.BadParameter(
                "Muxdev Workbench is not running; run `muxdev serve` in a project first"
            )
        registry.store.update_project(project_id, touch=True)
        _print_project_url(project_id, state)
    finally:
        registry.store.close()


@app.command("serve")
def serve(
    workspace: Workspace = Path.cwd(),
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_remote: Annotated[bool, typer.Option("--allow-remote")] = False,
    trusted_origin: Annotated[list[str] | None, typer.Option("--trusted-origin")] = None,
) -> None:
    import uvicorn

    workspace = workspace.resolve()
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not loopback and not allow_remote:
        raise typer.BadParameter("non-loopback Web binding requires --allow-remote")
    registry = WorkbenchRegistry.user()
    try:
        project = registry.register(workspace)
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as exc:
        registry.store.close()
        raise typer.BadParameter(str(exc)) from exc
    project_id = str(project["project_id"])

    state = _daemon_state()
    if state:
        health = _daemon_health(state)
        if health:
            registry.store.update_project(project_id, touch=True)
            _print_project_url(project_id, state)
            registry.store.close()
            return
        pid = int(state.get("pid") or 0)
        if _pid_is_alive(pid):
            registry.store.close()
            raise typer.BadParameter(
                "recorded Muxdev Daemon is alive but unhealthy; stop it before restarting"
            )
        _clear_daemon_state(str(state.get("instance_id") or "") or None)

    probe = {"host": host, "port": port, "instance_id": ""}
    discovered = _daemon_health(probe)
    if discovered:
        recovered = {
            "instance_id": str(discovered.get("instance_id") or ""),
            "pid": int(discovered.get("pid") or 0),
            "host": str(discovered.get("host") or host),
            "port": int(discovered.get("port") or port),
            "started_at": str(discovered.get("started_at") or ""),
        }
        _write_daemon_state(recovered)
        _print_project_url(project_id, recovered)
        registry.store.close()
        return
    if _port_is_open(host, port):
        registry.store.close()
        raise typer.BadParameter(
            f"{host}:{port} is occupied by a non-Muxdev process; choose another --port"
        )

    pairing_code = secrets.token_urlsafe(8) if allow_remote else None
    if pairing_code:
        console.print("Remote Web access is protected. Pair a browser with this one-time code:")
        console.print(f"[bold]{pairing_code}[/bold]")
    instance_id = f"daemon_{uuid4().hex}"
    started_at = utc_now()
    state = {
        "instance_id": instance_id,
        "pid": os.getpid(),
        "host": host,
        "port": port,
        "started_at": started_at,
    }
    _write_daemon_state(state)
    _print_project_url(project_id, state)
    try:
        uvicorn.run(
            create_app(
                workspace,
                workbench=registry,
                instance_id=instance_id,
                host=host,
                port=port,
                require_auth=allow_remote,
                pairing_code=pairing_code,
                trusted_origins=tuple(trusted_origin or ()),
            ),
            host=host,
            port=port,
        )
    finally:
        _clear_daemon_state(instance_id)
        registry.store.close()


if __name__ == "__main__":
    app()
