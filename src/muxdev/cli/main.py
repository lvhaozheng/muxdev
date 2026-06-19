"""Typer command surface for the muxdev local orchestration CLI.

This module intentionally stays thin: command handlers translate CLI arguments
into service/runtime calls, normalize JSON versus Rich output, and avoid owning
provider, workflow, or storage rules directly. Keeping that boundary clear makes
the same lower-level behavior reusable from the TUI, MCP server, tests, and
future API surfaces.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import __version__
from ..clients.daemon import DaemonConnectionError
from ..config.runtime import (
    config_check as runtime_config_check,
    global_config_path,
    load_runtime_config,
    parse_role_overrides,
    project_config_path,
    resolve_task_request,
    runtime_config_sources,
    set_runtime_config_value,
    setup_muxdev,
    write_full_presets,
    PROFILES,
    GATES,
    WORKFLOW_ALIASES,
    dumps_toml,
)
from ..core.platforms import follow_file_command, hidden_subprocess_kwargs, split_command_line
from ..models import ApprovalStatus
from ..api.mcp import handle_jsonrpc, mcp_doctor, server_manifest
from ..services.orchestration import deep_agent_task_pack, workflow_to_langgraph
from ..services.automation import render_why
from ..services.design import latest_design_contract
from ..config.loader import config_sources, load_config, path_config, validate_config
from ..services.rag import LocalRagIndex
from ..services.reports import generate_final_report
from ..services.evidence import cleanup_legacy_evidence, load_evidence_artifacts, render_evidence_text, verify_run_evidence, write_evidence_run
from ..services.advanced_parallel import detect_parallel_conflicts, record_parallel_conflicts
from ..services.dashboard_run import dashboard_path, write_run_dashboard
from ..services.flows import FlowRegistry
from ..services.multirepo import plan_multi_repo_orchestration
from ..services.provider_learning import refresh_provider_learning
from ..services.product_experience import build_product_experience, write_project_context
from ..services.workflow_plugins import get_workflow_plugin, list_workflow_plugins, render_plugin_command
from ..services.validation import load_validation_experiment, run_validation_experiment
from ..services.skills import (
    abtest_skill,
    activate_skill,
    add_skill,
    bind_skill,
    build_skill_catalog,
    eval_skill,
    export_skill,
    load_skills_config,
    remove_skill,
    resolve_active_skills,
    scan_skills,
    score_skill,
    select_skills,
    set_skill_policy,
    SkillRegistry,
    skill_doctor,
    skill_show,
    sync_skills,
    unbind_skill,
    validate_skill_path,
    verify_skill_lock,
    write_skill_lock,
)
from ..runtime import SupervisorRuntime
from ..core.safety import SafetyPolicyEngine
from ..clients.sessions import SessionManager, TmuxBackend
from ..daemon.paths import DEFAULT_API_PORT, DEFAULT_HOST, DEFAULT_UI_PORT, default_daemon_paths
from ..daemon.process import daemon_status as daemon_process_status
from ..daemon.process import start_daemon, stop_daemon
from ..storage import Blackboard, MemoryStore, RunStore, compact_trace, read_trace
from ..ui.repl import start_repl
from ..ui.tui import status_panel
from ..workflows import SOFTWARE_DEV_WORKFLOW
from .common import (
    _daemon_client,
    _parse_csv,
    _print_json,
    _print_service_started,
    _role_providers,
)
from .providers import provider_app
from .tui import (
    _daemon_error_hint,
    _daemon_error_panel,
    _handle_daemon_tui_command,
    _render_daemon_tui,
    _render_daemon_tui_frame,
    _start_daemon_tui,
)


REMOVED_TOP_LEVEL_ALIASES = {"run", "resume", "web", "account", "install"}


class NaturalTaskGroup(typer.core.TyperGroup):
    """Route unknown top-level text to the default dev command."""

    def resolve_command(self, ctx: typer.Context, args: list[str]):
        if args:
            cmd_name = str(args[0])
            command = super().get_command(ctx, cmd_name)
            if command is None and ctx.token_normalize_func is not None:
                command = super().get_command(ctx, ctx.token_normalize_func(cmd_name))
            if command is None and cmd_name not in REMOVED_TOP_LEVEL_ALIASES and not cmd_name.startswith("-"):
                dev_command = super().get_command(ctx, "dev")
                if dev_command is not None:
                    return "dev", dev_command, args
        return super().resolve_command(ctx, args)


# The root app uses invoke_without_command so plain `muxdev` can open the TUI
# instead of printing help. Automation should call explicit subcommands.
app = typer.Typer(
    help="muxdev local AI Coding CLI control plane",
    cls=NaturalTaskGroup,
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
policy_app = typer.Typer(help="Safety policy tools")
trace_app = typer.Typer(help="Trace inspection tools")
skill_app = typer.Typer(help="Skill registry tools")
preset_app = typer.Typer(help="Built-in profile, gate, and workflow presets")
mcp_app = typer.Typer(help="MCP server tools")
session_app = typer.Typer(help="Long-lived provider session tools")
rag_app = typer.Typer(help="Local retrieval index tools")
graph_app = typer.Typer(help="Workflow graph export tools")
deep_agent_app = typer.Typer(help="Deep-agent integration tools")
workflow_app = typer.Typer(help="Workflow template catalog tools")
plugin_app = typer.Typer(
    help="Deprecated plugin registry tools",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
flow_app = typer.Typer(help="Scheduled flow tools")
config_app = typer.Typer(help="Configuration inspection tools", invoke_without_command=True, no_args_is_help=False)
memory_app = typer.Typer(help="Explicit project memory tools")
parallel_app = typer.Typer(help="Advanced parallel-squad tools")
learning_app = typer.Typer(help="Long-term learning tools")
multirepo_app = typer.Typer(help="Multi-repo orchestration tools")
ci_app = typer.Typer(help="CI rescue commands")
evidence_app = typer.Typer(help="Evidence v2 event, manifest, and evaluation tools")
action_app = typer.Typer(help="Provider action handoff tools")
feedback_app = typer.Typer(help="External feedback routing tools")
cache_app = typer.Typer(help="Content-addressed cache tools")
validate_app = typer.Typer(help="Multi-agent validation experiment tools")
app.add_typer(provider_app, name="provider")
app.add_typer(policy_app, name="policy")
app.add_typer(trace_app, name="trace")
app.add_typer(skill_app, name="skill")
app.add_typer(plugin_app, name="plugin")
app.add_typer(preset_app, name="preset")
app.add_typer(mcp_app, name="mcp")
app.add_typer(session_app, name="session")
app.add_typer(rag_app, name="rag")
app.add_typer(graph_app, name="graph")
app.add_typer(deep_agent_app, name="deep-agent")
app.add_typer(workflow_app, name="workflow")
app.add_typer(flow_app, name="flow")
app.add_typer(config_app, name="config")
app.add_typer(memory_app, name="memory")
app.add_typer(parallel_app, name="parallel")
app.add_typer(learning_app, name="learning")
app.add_typer(multirepo_app, name="multirepo")
app.add_typer(ci_app, name="ci")
app.add_typer(evidence_app, name="evidence")
app.add_typer(action_app, name="action")
app.add_typer(feedback_app, name="feedback")
app.add_typer(cache_app, name="cache")
app.add_typer(validate_app, name="validate")
console = Console(width=320)


@evidence_app.callback()
def evidence_group() -> None:
    """Evidence v2 tools."""


@evidence_app.command("latest")
def evidence_latest(
    events: Annotated[bool, typer.Option("--events", help="Include the first Evidence v2 events.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show the latest run's Evidence v2 evaluation."""
    _show_evidence("latest", include_events=events, json_output=json_output)


@evidence_app.command("show")
def evidence_show(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    events: Annotated[bool, typer.Option("--events", help="Include the first Evidence v2 events.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show one run's Evidence v2 evaluation."""
    _show_evidence(run_id, include_events=events, json_output=json_output)


def _show_evidence(run_id: str, *, include_events: bool, json_output: bool) -> None:
    resolved, run_dir = _resolve_evidence_run(run_id)
    with _evidence_blackboard(run_dir) as blackboard:
        payload = load_evidence_artifacts(run_dir, resolved, blackboard)
        if not payload.get("evaluation") or not payload.get("manifest"):
            write_evidence_run(run_dir, resolved, blackboard)
            payload = load_evidence_artifacts(run_dir, resolved, blackboard)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(render_evidence_text(payload, include_events=include_events), title="muxdev evidence"))


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", help="Show muxdev version and exit."),
    ] = False,
) -> None:
    """Handle global flags and the no-subcommand TUI entrypoint."""
    if version:
        typer.echo(f"muxdev {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        task = " ".join(str(arg) for arg in ctx.args).strip()
        if task:
            if task in REMOVED_TOP_LEVEL_ALIASES:
                raise typer.BadParameter(f"unknown command: {task}")
            _submit_main_task(
                "dev",
                task=task,
                profile=None,
                gate=None,
                role=None,
                skill=None,
                task_file=None,
                provider=None,
                workflow=None,
                require_approval="",
                max_cost_usd=0.5,
                host=DEFAULT_HOST,
                port=DEFAULT_API_PORT,
                json_output=False,
                title="muxdev dev",
            )
            raise typer.Exit()
        _start_daemon_tui("latest", host=DEFAULT_HOST, port=DEFAULT_API_PORT)
        raise typer.Exit()


@app.command()
def start(
    host: Annotated[str, typer.Option("--host", help="Daemon bind host.")] = DEFAULT_HOST,
    api_port: Annotated[int, typer.Option("--api-port", help="Daemon API port.")] = DEFAULT_API_PORT,
    ui_port: Annotated[int, typer.Option("--ui-port", help="Dashboard port.")] = DEFAULT_UI_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Start the muxdev daemon in the background."""
    payload = start_daemon(host=host, api_port=api_port, ui_port=ui_port)
    if json_output:
        _print_json(payload)
        return
    _print_service_started(payload)


@app.command()
def serve(
    daemon: Annotated[bool, typer.Option("--daemon", help="Start daemon in the background.")] = False,
    status: Annotated[bool, typer.Option("--status", help="Show daemon status.")] = False,
    stop_flag: Annotated[bool, typer.Option("--stop", help="Stop the daemon.")] = False,
    restart: Annotated[bool, typer.Option("--restart", help="Restart the daemon.")] = False,
    host: Annotated[str, typer.Option("--host", help="Daemon bind host.")] = DEFAULT_HOST,
    api_port: Annotated[int, typer.Option("--api-port", help="Daemon API port.")] = DEFAULT_API_PORT,
    ui_port: Annotated[int, typer.Option("--ui-port", help="Dashboard port.")] = DEFAULT_UI_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Run or manage the muxdev daemon."""
    if status:
        payload = daemon_process_status()
    elif stop_flag:
        payload = stop_daemon(host=host, api_port=api_port, ui_port=ui_port)
    elif restart:
        stop_daemon(host=host, api_port=api_port, ui_port=ui_port)
        payload = start_daemon(host=host, api_port=api_port, ui_port=ui_port)
    elif daemon:
        payload = start_daemon(host=host, api_port=api_port, ui_port=ui_port)
    else:
        from ..daemon.server import main as serve_main

        serve_main(["--host", host, "--api-port", str(api_port), "--ui-port", str(ui_port)])
        return
    if json_output:
        _print_json(payload)
        return
    _print_service_started(payload)


@app.command()
def dashboard(
    host: Annotated[str, typer.Option("--host", help="Dashboard host.")] = DEFAULT_HOST,
    ui_port: Annotated[int, typer.Option("--ui-port", help="Dashboard port.")] = DEFAULT_UI_PORT,
    api_port: Annotated[int, typer.Option("--api-port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Print the daemon Dashboard URL."""
    payload = {"dashboard": f"http://{host}:{ui_port}", "api": f"http://{host}:{api_port}"}
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Dashboard"))


@app.command()
def setup(
    global_scope: Annotated[bool, typer.Option("--global", help="Write ~/.muxdev/config.toml.")] = False,
    project: Annotated[bool, typer.Option("--project", help="Write ./.muxdev/config.toml.")] = False,
    check: Annotated[bool, typer.Option("--check", help="Check providers and paths without writing config.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Accept recommended configuration.")] = False,
    full: Annotated[bool, typer.Option("--full", help="Also materialize advanced preset files.")] = False,
    interactive: Annotated[bool, typer.Option("--interactive", help="Compatibility alias for the default setup flow.")] = False,
    apply: Annotated[bool, typer.Option("--apply", help="Compatibility alias for --yes.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Bootstrap the TOML-first muxdev configuration."""
    project_scope = project and not global_scope
    payload = setup_muxdev(Path.cwd(), global_config=not project_scope, project=project_scope, check=check, yes=yes or apply, full=full)
    if json_output:
        _print_json(payload)
        return
    lines = [
        f"status: {payload['status']}",
        f"scope: {payload['scope']}",
        f"target: {payload['target']}",
        f"providers cache: {payload['providers_cache']}",
        f"written: {payload['written']}",
    ]
    context = payload.get("project_context")
    if isinstance(context, dict):
        lines.append(f"project context: {context.get('path')} ({'written' if context.get('written') else 'kept'})")
    provider_setup = payload.get("provider_setup")
    if isinstance(provider_setup, dict):
        steps = provider_setup.get("steps", [])
        if isinstance(steps, list):
            lines.append(f"provider setup steps: {len(steps)}")
    console.print(Panel("\n".join(lines), title="muxdev setup"))


@app.command("init")
def init_wizard(
    wizard: Annotated[bool, typer.Option("--wizard", help="Run the guided first-use setup flow.")] = True,
    global_scope: Annotated[bool, typer.Option("--global", help="Write ~/.muxdev/config.toml instead of project config.")] = False,
    full: Annotated[bool, typer.Option("--full", help="Also materialize advanced preset files.")] = False,
    host: Annotated[str, typer.Option("--host", help="Daemon API host for checks.")] = DEFAULT_HOST,
    api_port: Annotated[int, typer.Option("--api-port", help="Daemon API port for checks.")] = DEFAULT_API_PORT,
    ui_port: Annotated[int, typer.Option("--ui-port", help="Dashboard port for checks.")] = DEFAULT_UI_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Guided first-use setup for a workspace."""
    setup_payload = setup_muxdev(Path.cwd(), global_config=global_scope, project=not global_scope, yes=True, full=full)
    doctor_payload = runtime_config_check(Path.cwd(), host=host, api_port=api_port, ui_port=ui_port)
    payload = {
        "status": "ready" if doctor_payload["valid"] else "needs_attention",
        "wizard": wizard,
        "setup": setup_payload,
        "doctor": doctor_payload,
        "next": [
            "muxdev demo --mock",
            "muxdev start",
            'muxdev dev "describe your first task"',
        ],
    }
    if json_output:
        _print_json(payload)
        return
    lines = [
        "Guided setup complete.",
        f"config: {setup_payload['target']}",
        f"status: {payload['status']}",
        "",
        "Next:",
        "  1. muxdev demo --mock",
        "  2. muxdev start",
        '  3. muxdev dev "describe your first task"',
    ]
    console.print(Panel("\n".join(lines), title="muxdev init"))
    _print_doctor_checks(doctor_payload)


@app.command()
def doctor(
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    api_port: Annotated[int, typer.Option("--api-port", help="Daemon API port.")] = DEFAULT_API_PORT,
    ui_port: Annotated[int, typer.Option("--ui-port", help="Dashboard port.")] = DEFAULT_UI_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Check first-use readiness for daemon, providers, Git, memory, ports, and mock demo."""
    payload = runtime_config_check(Path.cwd(), host=host, api_port=api_port, ui_port=ui_port)
    if json_output:
        _print_json(payload)
        return
    _print_doctor_checks(payload)


@app.command()
def demo(
    mock: Annotated[bool, typer.Option("--mock/--no-mock", help="Use the deterministic offline mock provider.")] = True,
    task: Annotated[str, typer.Option("--task", help="Demo task text.")] = "run the first muxdev mock task",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Run a complete offline demo so new users can see muxdev without provider setup."""
    if not mock:
        raise typer.BadParameter("demo currently supports the deterministic `--mock` path")
    provider = "mock"
    result = SupervisorRuntime(Path.cwd()).run(
        task,
        provider=provider,
        workflow_name="dev",
        require_approval=set(),
        profile="solo",
        gate="auto",
        depth="simple",
        topology="solo",
        automation={"intent": "dev", "depth": "simple", "topology": "solo", "roles": ["code", "test", "review"]},
    )
    payload = {
        "run_id": result.run_id,
        "status": str(result.status),
        "provider": provider,
        "run_dir": str(result.run_dir),
        "report": str(result.report_path) if result.report_path else None,
        "next": [
            f"muxdev report {result.run_id}",
            f"muxdev diff {result.run_id}",
            "muxdev start",
        ],
    }
    if json_output:
        _print_json(payload)
        return
    lines = [
        f"Demo run: {result.run_id}",
        f"status: {result.status}",
        f"provider: {provider}",
        f"run dir: {result.run_dir}",
    ]
    if result.report_path:
        lines.append(f"report: {result.report_path}")
    lines.extend(
        [
            "",
            "Next:",
            f"  muxdev report {result.run_id}",
            f"  muxdev diff {result.run_id}",
            "  muxdev start",
        ]
    )
    console.print(Panel("\n".join(lines), title="muxdev demo"))


@app.command("context")
def context_file(
    write: Annotated[bool, typer.Option("--write", help="Create or update MUXDEV.md.")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Overwrite an existing MUXDEV.md.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show or create the project context file used by muxdev."""
    if write or overwrite:
        payload = write_project_context(Path.cwd(), overwrite=overwrite)
    else:
        payload = build_product_experience(Path.cwd())["project_context"]
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items() if key != "preview"), title="MUXDEV.md"))


@app.command("experience")
def product_experience(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show the polished product surface: install, setup, budget, safety, skills, and providers."""
    payload = build_product_experience(Path.cwd())
    if json_output:
        _print_json(payload)
        return
    quickstart = payload["quickstart"]
    budget = payload["budget"]
    git = payload["git_safety"]
    lines = [
        f"install: {quickstart['one_line_install']}",
        f"first run: {' -> '.join(quickstart['first_run'])}",
        f"budget: ${budget['total_cost_usd']} / {budget['total_tokens']} tokens",
        f"git: {git['status']}",
        f"context: {payload['project_context']['path']}",
        "",
        "Next:",
        "  muxdev setup --project",
        "  muxdev provider setup",
        "  muxdev dashboard",
    ]
    console.print(Panel("\n".join(lines), title="muxdev product experience"))


def _print_doctor_checks(payload: dict[str, object]) -> None:
    checks = payload.get("checks", [])
    table = Table(title="First-use readiness")
    table.add_column("Status")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Fix")
    for row in checks if isinstance(checks, list) else []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "-")
        style = {"pass": "green", "warn": "yellow", "fail": "red"}.get(status, "white")
        table.add_row(
            Text(status.upper(), style=style),
            str(row.get("label") or row.get("id") or "-"),
            str(row.get("summary") or "-"),
            str(row.get("fix") or ""),
        )
    console.print(table)
    lines = [
        f"valid: {payload['valid']}",
        f"errors: {len(payload.get('errors', []))}",
        f"warnings: {len(payload.get('warnings', []))}",
        f"profile: {payload.get('effective', {}).get('profile', '-') if isinstance(payload.get('effective'), dict) else '-'}",
        f"gate: {payload.get('effective', {}).get('gate', '-') if isinstance(payload.get('effective'), dict) else '-'}",
    ]
    if payload.get("errors"):
        lines.append("")
        lines.extend(f"error: {error}" for error in payload.get("errors", []) if error)
    if payload.get("warnings"):
        lines.append("")
        lines.extend(f"warning: {warning}" for warning in payload.get("warnings", []) if warning)
    lines.extend(["", "Fast path: `muxdev demo --mock` works without external providers."])
    console.print(Panel("\n".join(lines), title="muxdev doctor"))


@app.command()
def dev(
    task: Annotated[str | None, typer.Argument(help="Development task to submit.")] = None,
    profile: Annotated[str | None, typer.Option("-p", "--profile", help="Profile: solo, pair, squad, ci.")] = None,
    gate: Annotated[str | None, typer.Option("-g", "--gate", help="Gate: auto, safe, strict, ci.")] = None,
    simple: Annotated[bool, typer.Option("--simple", help="Force simple auto flow depth.")] = False,
    safe_depth: Annotated[bool, typer.Option("--safe", help="Force safe auto flow depth.")] = False,
    deep: Annotated[bool, typer.Option("--deep", help="Force deep auto flow depth.")] = False,
    parallel: Annotated[bool, typer.Option("--parallel", help="Force parallel auto flow depth.")] = False,
    role: Annotated[list[str] | None, typer.Option("--role", help="Role provider override, e.g. --role code=codex.")] = None,
    skill: Annotated[list[str] | None, typer.Option("-s", "--skill", help="Skill activation, e.g. -s review=security-review.")] = None,
    task_file: Annotated[Path | None, typer.Option("-f", "--file", help="Task TOML file.")] = None,
    from_design: Annotated[str | None, typer.Option("--from-design", help="Use a design contract path or 'latest'.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Fallback provider for all roles.")] = None,
    workflow: Annotated[str | None, typer.Option("--workflow", help="Workflow name or YAML path.")] = None,
    require_approval: Annotated[str, typer.Option("--require-approval", help="Comma-separated extra approval types.")] = "",
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", help="Budget limit for this task.")] = 0.5,
    bg: Annotated[bool, typer.Option("--bg", help="Compatibility flag; daemon tasks always run in the background.")] = False,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Submit a development task through the daemon main path."""
    _submit_main_task(
        "dev",
        task=task,
        profile=profile,
        gate=gate,
        depth=_depth_override(simple=simple, safe=safe_depth, deep=deep, parallel=parallel),
        role=role,
        skill=skill,
        task_file=task_file,
        from_design=from_design,
        provider=provider,
        workflow=workflow,
        require_approval=require_approval,
        max_cost_usd=max_cost_usd,
        host=host,
        port=port,
        json_output=json_output,
        title="muxdev dev",
    )


@app.command()
def fix(
    task: Annotated[str | None, typer.Argument(help="Issue or bug to fix.")] = None,
    profile: Annotated[str | None, typer.Option("-p", "--profile", help="Profile: solo, pair, squad, ci.")] = None,
    gate: Annotated[str | None, typer.Option("-g", "--gate", help="Gate: auto, safe, strict, ci.")] = None,
    role: Annotated[list[str] | None, typer.Option("--role", help="Role provider override.")] = None,
    skill: Annotated[list[str] | None, typer.Option("-s", "--skill", help="Skill activation.")] = None,
    task_file: Annotated[Path | None, typer.Option("-f", "--file", help="Task TOML file.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Fallback provider.")] = None,
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", help="Budget limit for this task.")] = 0.5,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Submit a focused fix task."""
    _submit_main_task("fix", task=task, profile=profile, gate=gate, role=role, skill=skill, task_file=task_file, provider=provider, workflow="fix", require_approval="", max_cost_usd=max_cost_usd, host=host, port=port, json_output=json_output, title="muxdev fix")


@app.command()
def review(
    task: Annotated[str | None, typer.Argument(help="Review task description.")] = None,
    profile: Annotated[str | None, typer.Option("-p", "--profile", help="Profile: solo, pair, squad, ci.")] = None,
    gate: Annotated[str | None, typer.Option("-g", "--gate", help="Gate: auto, safe, strict, ci.")] = None,
    role: Annotated[list[str] | None, typer.Option("--role", help="Role provider override.")] = None,
    skill: Annotated[list[str] | None, typer.Option("-s", "--skill", help="Skill activation.")] = None,
    task_file: Annotated[Path | None, typer.Option("-f", "--file", help="Task TOML file.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Fallback provider.")] = None,
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", help="Budget limit for this task.")] = 0.5,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Submit a review-only task."""
    _submit_main_task("review", task=task, profile=profile, gate=gate, role=role, skill=skill, task_file=task_file, provider=provider, workflow="review", require_approval="", max_cost_usd=max_cost_usd, host=host, port=port, json_output=json_output, title="muxdev review")


@app.command("test")
def test_command(
    task: Annotated[str | None, typer.Argument(help="Test task description.")] = None,
    profile: Annotated[str | None, typer.Option("-p", "--profile", help="Profile: solo, pair, squad, ci.")] = None,
    gate: Annotated[str | None, typer.Option("-g", "--gate", help="Gate: auto, safe, strict, ci.")] = None,
    role: Annotated[list[str] | None, typer.Option("--role", help="Role provider override.")] = None,
    skill: Annotated[list[str] | None, typer.Option("-s", "--skill", help="Skill activation.")] = None,
    task_file: Annotated[Path | None, typer.Option("-f", "--file", help="Task TOML file.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Fallback provider.")] = None,
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", help="Budget limit for this task.")] = 0.5,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Submit a test-only task."""
    _submit_main_task("test", task=task, profile=profile, gate=gate, role=role, skill=skill, task_file=task_file, provider=provider, workflow="test", require_approval="", max_cost_usd=max_cost_usd, host=host, port=port, json_output=json_output, title="muxdev test")


@app.command()
def design(
    task: Annotated[str | None, typer.Argument(help="Design task to submit.")] = None,
    profile: Annotated[str | None, typer.Option("-p", "--profile", help="Profile: auto, solo, pair, squad, ci.")] = None,
    gate: Annotated[str | None, typer.Option("-g", "--gate", help="Gate: auto, safe, strict, ci.")] = None,
    simple: Annotated[bool, typer.Option("--simple", help="Force simple auto flow depth.")] = False,
    safe_depth: Annotated[bool, typer.Option("--safe", help="Force safe auto flow depth.")] = False,
    deep: Annotated[bool, typer.Option("--deep", help="Force deep auto flow depth.")] = False,
    role: Annotated[list[str] | None, typer.Option("--role", help="Role provider override.")] = None,
    skill: Annotated[list[str] | None, typer.Option("-s", "--skill", help="Skill activation.")] = None,
    task_file: Annotated[Path | None, typer.Option("-f", "--file", help="Task TOML file.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Fallback provider.")] = None,
    workflow: Annotated[str | None, typer.Option("--workflow", help="Workflow name or YAML path, e.g. design-v2.")] = None,
    max_review_fixes: Annotated[int, typer.Option("--max-review-fixes", help="Maximum design review revision loops for design-v2.")] = 2,
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", help="Budget limit for this task.")] = 0.5,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Submit a design-only task that produces a design pack."""
    _submit_main_task(
        "design",
        task=task,
        profile=profile,
        gate=gate or "auto",
        role=role,
        skill=skill,
        task_file=task_file,
        provider=provider,
        workflow=workflow,
        require_approval="design" if workflow == "design-v2" and (gate or "auto") != "auto" else "",
        max_cost_usd=max_cost_usd,
        host=host,
        port=port,
        json_output=json_output,
        title="muxdev design",
        depth=_depth_override(simple=simple, safe=safe_depth, deep=deep, parallel=False),
        max_review_fixes=max_review_fixes,
    )


@app.command()
def refactor(
    task: Annotated[str | None, typer.Argument(help="Refactor task to submit.")] = None,
    profile: Annotated[str | None, typer.Option("-p", "--profile", help="Profile: auto, solo, pair, squad, ci.")] = None,
    gate: Annotated[str | None, typer.Option("-g", "--gate", help="Gate: auto, safe, strict, ci.")] = None,
    simple: Annotated[bool, typer.Option("--simple", help="Force simple auto flow depth.")] = False,
    safe_depth: Annotated[bool, typer.Option("--safe", help="Force safe auto flow depth.")] = False,
    deep: Annotated[bool, typer.Option("--deep", help="Force deep auto flow depth.")] = False,
    parallel: Annotated[bool, typer.Option("--parallel", help="Force parallel auto flow depth.")] = False,
    role: Annotated[list[str] | None, typer.Option("--role", help="Role provider override.")] = None,
    skill: Annotated[list[str] | None, typer.Option("-s", "--skill", help="Skill activation.")] = None,
    task_file: Annotated[Path | None, typer.Option("-f", "--file", help="Task TOML file.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Fallback provider.")] = None,
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", help="Budget limit for this task.")] = 0.5,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Submit a refactor task through the auto-orchestrated dev workflow."""
    _submit_main_task("refactor", task=task, profile=profile, gate=gate, role=role, skill=skill, task_file=task_file, provider=provider, workflow="dev", require_approval="", max_cost_usd=max_cost_usd, host=host, port=port, json_output=json_output, title="muxdev refactor", depth=_depth_override(simple=simple, safe=safe_depth, deep=deep, parallel=parallel))


@ci_app.command("fix")
def ci_fix(
    task: Annotated[str | None, typer.Argument(help="CI failure description.")] = None,
    role: Annotated[list[str] | None, typer.Option("--role", help="Role provider override.")] = None,
    skill: Annotated[list[str] | None, typer.Option("-s", "--skill", help="Skill activation.")] = None,
    task_file: Annotated[Path | None, typer.Option("-f", "--file", help="Task TOML file.")] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Fallback provider.")] = None,
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", help="Budget limit for this task.")] = 0.5,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Submit a non-interactive CI rescue task."""
    _submit_main_task("ci", task=task or "fix CI failures", profile="ci", gate="ci", role=role, skill=skill, task_file=task_file, provider=provider, workflow="dev", require_approval="", max_cost_usd=max_cost_usd, host=host, port=port, json_output=json_output, title="muxdev ci fix", depth="ci")


@ci_app.command("rescue")
def ci_rescue(
    content: Annotated[str, typer.Argument(help="CI log, failure summary, or URL.")],
    source: Annotated[str, typer.Option("--source", help="Feedback source label.")] = "ci",
    run_id: Annotated[str | None, typer.Option("--run-id", help="Related muxdev run id.")] = None,
    provider: Annotated[str, typer.Option("--provider", help="Provider for the rescue task.")] = "mock",
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Route CI failure feedback and auto-submit a rescue task."""
    payload = _daemon_client(host, port).feedback(
        {
            "kind": "ci_failed",
            "source": source,
            "content": content,
            "workspace": str(Path.cwd()),
            "run_id": run_id,
            "severity": "high",
            "provider": provider,
            "auto_submit": True,
        }
    )
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="CI Rescue"))


@feedback_app.command("add")
def feedback_add(
    kind: Annotated[str, typer.Argument(help="Feedback kind, e.g. ci_failed, review_comment, manual_feedback.")],
    content: Annotated[str, typer.Argument(help="Feedback text, log excerpt, URL, or comment.")],
    source: Annotated[str, typer.Option("--source", help="Feedback source label.")] = "manual",
    run_id: Annotated[str | None, typer.Option("--run-id", help="Related muxdev run id.")] = None,
    severity: Annotated[str, typer.Option("--severity", help="low/medium/high.")] = "medium",
    provider: Annotated[str, typer.Option("--provider", help="Provider for auto-submitted rescue tasks.")] = "mock",
    auto_submit: Annotated[bool, typer.Option("--auto-submit/--no-auto-submit", help="Auto-submit routed tasks when rules allow.")] = True,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Ingest external feedback into the muxdev feedback router."""
    payload = _daemon_client(host, port).feedback(
        {
            "kind": kind,
            "source": source,
            "content": content,
            "workspace": str(Path.cwd()),
            "run_id": run_id,
            "severity": severity,
            "provider": provider,
            "auto_submit": auto_submit,
        }
    )
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Feedback Routed"))


@feedback_app.command("list")
def feedback_list(
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List feedback router state from the daemon."""
    payload = _daemon_client(host, port).ecosystem()
    rows = payload.get("feedback_events", []) if isinstance(payload, dict) else []
    if json_output:
        _print_json(rows)
        return
    table = Table(title="Feedback Events")
    for column in ("feedback_id", "kind", "source", "status", "route_to", "severity", "content"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("feedback_id", "kind", "source", "status", "route_to", "severity", "content")))
    console.print(table)


@cache_app.command("list")
def cache_list(
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List CAS cache entries recorded by the daemon."""
    payload = _daemon_client(host, port).ecosystem()
    rows = payload.get("cache_entries", []) if isinstance(payload, dict) else []
    if json_output:
        _print_json(rows)
        return
    table = Table(title="CAS Cache")
    for column in ("cache_key", "kind", "path", "value_hash", "last_accessed_at"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("cache_key", "kind", "path", "value_hash", "last_accessed_at")))
    console.print(table)


@validate_app.command("run")
def validate_run(
    suite: Annotated[str, typer.Argument(help="Suite path or validation/suites/<name>.yaml.")],
    strategies: Annotated[str, typer.Option("--strategies", help="Comma-separated strategies: direct_cli,muxdev_single_cli,muxdev_multi_cli.")] = "direct_cli,muxdev_single_cli,muxdev_multi_cli",
    provider: Annotated[str, typer.Option("--provider", help="Provider to use for all strategy runs.")] = "mock",
    role: Annotated[list[str] | None, typer.Option("--role", help="Role provider override for muxdev_multi_cli, e.g. --role code=codex.")] = None,
    multi_workflow: Annotated[str, typer.Option("--multi-workflow", help="Workflow for muxdev_single_cli and muxdev_multi_cli strategies.")] = "software-dev",
    judge_provider: Annotated[str | None, typer.Option("--judge-provider", help="Optional provider used for LLM-as-a-Judge scoring.")] = None,
    judge_weight: Annotated[float, typer.Option("--judge-weight", help="Weight for judge score when --judge-provider is set.")] = 0.2,
    experiment_id: Annotated[str | None, typer.Option("--experiment-id", help="Optional deterministic experiment id.")] = None,
    export: Annotated[str, typer.Option("--export", help="Comma-separated exporters: local,langfuse,langsmith.")] = "local",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Run a validation experiment comparing direct CLI and muxdev orchestration."""
    try:
        role_providers = parse_role_overrides(role)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    experiment = run_validation_experiment(
        Path.cwd(),
        suite,
        strategies=[item.strip() for item in strategies.split(",") if item.strip()],
        provider=provider,
        multi_workflow=multi_workflow,
        role_providers=role_providers,
        judge_provider=judge_provider,
        judge_weight=judge_weight,
        experiment_id=experiment_id,
        export_targets=_parse_csv(export),
    )
    payload = experiment.model_dump()
    if json_output:
        _print_json(payload)
        return
    comparison = experiment.comparison
    lines = [
        f"experiment_id: {experiment.experiment_id}",
        f"suite: {experiment.suite.name}",
        f"winner: {comparison.winner if comparison else '-'}",
        f"report: {experiment.artifacts.get('report', '-')}",
    ]
    console.print(Panel("\n".join(lines), title="Validation Experiment"))


@validate_app.command("report")
def validate_report(
    experiment_id: Annotated[str, typer.Argument(help="Validation experiment id.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show a validation experiment report path and summary."""
    experiment = load_validation_experiment(Path.cwd(), experiment_id)
    if json_output:
        _print_json(experiment.model_dump())
        return
    report = experiment.artifacts.get("report")
    comparison = experiment.comparison
    table = Table(title=f"Validation {experiment.experiment_id}")
    for column in ("strategy", "score"):
        table.add_column(column)
    for strategy, score in (comparison.strategy_scores if comparison else {}).items():
        table.add_row(strategy, str(score))
    console.print(table)
    console.print(Panel(f"winner: {comparison.winner if comparison else '-'}\nreport: {report or '-'}", title="Validation Report"))


@app.command()
def why(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Explain why muxdev selected the current intent, flow depth, and roles."""
    payload = _load_run_context(run_id, host=host, port=port)
    context = payload.get("context", {}) if isinstance(payload, dict) else {}
    automation = context.get("automation", context) if isinstance(context, dict) else {}
    if json_output:
        _print_json({"run_id": payload.get("run_id", run_id) if isinstance(payload, dict) else run_id, "automation": automation})
        return
    console.print(Panel(render_why({"automation": automation}), title="muxdev why"))


@memory_app.command("status")
def memory_status(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show project memory database status."""
    with MemoryStore(Path.cwd()) as store:
        payload = store.status()
    if json_output:
        _print_json(payload)
        return
    lines = [f"path: {payload['path']}", f"total: {payload['total']}"]
    counts = payload.get("counts", {})
    if isinstance(counts, dict):
        lines.extend(f"{key}: {value}" for key, value in sorted(counts.items()))
    layers = payload.get("layers", {})
    if isinstance(layers, dict) and layers:
        lines.append("layers: " + ", ".join(f"{key}={value}" for key, value in sorted(layers.items())))
    inbox = payload.get("inbox", {})
    if isinstance(inbox, dict) and inbox:
        lines.append("inbox: " + ", ".join(f"{key}={value}" for key, value in sorted(inbox.items())))
    console.print(Panel("\n".join(lines), title="muxdev memory status"))


@memory_app.command("query")
def memory_query(
    query: Annotated[str, typer.Argument(help="Text to search in active project memory.")] = "",
    status: Annotated[str, typer.Option("--status", help="Memory status to search.")] = "active",
    layers: Annotated[str | None, typer.Option("--layers", help="Comma-separated memory layers to include.")] = None,
    scope_ids: Annotated[str | None, typer.Option("--scope-ids", help="Comma-separated scope ids to include.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum rows to return.")] = 8,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Search project memory."""
    with MemoryStore(Path.cwd()) as store:
        rows = store.query(query, status=status, layers=_csv_values(layers), scope_ids=_csv_values(scope_ids), limit=limit)
    if json_output:
        _print_json(rows)
        return
    table = Table(title="muxdev memory query")
    for column in ("layer", "scope_id", "id", "status", "promotion_state", "kind", "role", "claim"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("layer", "scope_id", "id", "status", "promotion_state", "kind", "role", "claim")))
    console.print(table)


@memory_app.command("propose")
def memory_propose(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    run_dir: Annotated[Path | None, typer.Option("--run-dir", help="Explicit run directory.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Explicitly propose memory items from a completed run."""
    resolved_run_dir = run_dir or RunStore(Path.cwd()).find_run_dir(_resolve_run_id(run_id))
    with MemoryStore(Path.cwd()) as store:
        rows = store.propose_from_run(resolved_run_dir, resolved_run_dir.name)
    if json_output:
        _print_json(rows)
        return
    console.print(Panel("\n".join(f"{row['id']}: {row['claim']}" for row in rows), title="muxdev memory propose"))


@memory_app.command("inbox")
def memory_inbox(
    limit: Annotated[int, typer.Option("--limit", help="Maximum rows per inbox bucket.")] = 50,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Review proposed, promotable, contradictory, quarantined, and expired memory."""
    with MemoryStore(Path.cwd()) as store:
        payload = store.inbox(limit=limit)
    if json_output:
        _print_json(payload)
        return
    lines: list[str] = []
    for key, value in payload.items():
        if isinstance(value, list):
            lines.append(f"{key}: {len(value)}")
            for row in value[:5]:
                if isinstance(row, dict):
                    ident = row.get("id") or row.get("contradiction_id") or "item"
                    label = row.get("claim") or row.get("reason") or row.get("status") or ""
                    lines.append(f"  {ident}: {label}")
    console.print(Panel("\n".join(lines) or "empty", title="muxdev memory inbox"))


@memory_app.command("promote")
def memory_promote(
    memory_id: Annotated[str, typer.Argument(help="Memory item id.")],
    layer: Annotated[str, typer.Option("--layer", help="Target layer: project/workspace/user.")] = "project",
    scope_id: Annotated[str | None, typer.Option("--scope-id", help="Optional target scope id.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Promote reviewed session/run/branch memory into a long-term layer."""
    try:
        with MemoryStore(Path.cwd()) as store:
            payload = store.promote(memory_id, layer=layer, scope_id=scope_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(f"{payload['id']}: {payload['layer']} / {payload['status']}", title="muxdev memory promote"))


@memory_app.command("approve")
def memory_approve(
    memory_id: Annotated[str, typer.Argument(help="Memory item id.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Approve a proposed memory item and make it active."""
    try:
        with MemoryStore(Path.cwd()) as store:
            payload = store.approve(memory_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(f"{payload['id']}: {payload['status']}", title="muxdev memory approve"))


@memory_app.command("quarantine")
def memory_quarantine(
    memory_id: Annotated[str, typer.Argument(help="Memory item id.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Quarantine an obsolete or unsafe memory item."""
    try:
        with MemoryStore(Path.cwd()) as store:
            payload = store.quarantine(memory_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(f"{payload['id']}: {payload['status']}", title="muxdev memory quarantine"))


@memory_app.command("contradictions")
def memory_contradictions(
    status: Annotated[str | None, typer.Option("--status", help="Filter by pending/quarantined/stale.")] = None,
    detect: Annotated[bool, typer.Option("--detect/--no-detect", help="Run contradiction detection before listing.")] = True,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Detect and list contradictory project memory records."""
    with MemoryStore(Path.cwd()) as store:
        if detect:
            store.detect_contradictions()
        rows = store.list_contradictions(status=status)
    if json_output:
        _print_json(rows)
        return
    table = Table(title="muxdev memory contradictions")
    for column in ("contradiction_id", "memory_id", "conflicting_memory_id", "status", "reason", "quarantine_target"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("contradiction_id", "memory_id", "conflicting_memory_id", "status", "reason", "quarantine_target")))
    console.print(table)


@memory_app.command("quarantine-auto")
def memory_quarantine_auto(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Automatically quarantine lower-confidence contradictory memory."""
    with MemoryStore(Path.cwd()) as store:
        store.detect_contradictions()
        rows = store.auto_quarantine_contradictions()
    if json_output:
        _print_json(rows)
        return
    console.print(Panel(f"quarantined: {len(rows)}", title="muxdev memory quarantine-auto"))


@parallel_app.command("conflicts")
def parallel_conflicts(
    plan_file: Annotated[Path | None, typer.Option("--file", help="JSON file mapping stage id to planned write paths.")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id", help="Filter or record against a run id.")] = None,
    status: Annotated[str | None, typer.Option("--status", help="Filter daemon conflict status.")] = None,
    record: Annotated[bool, typer.Option("--record", help="Persist detected conflicts in local ecosystem DB.")] = False,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Detect or list conflict-aware parallel-squad write conflicts."""
    if plan_file:
        try:
            data = json.loads(plan_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"invalid JSON file: {plan_file}") from exc
        if not isinstance(data, dict):
            raise typer.BadParameter("--file must contain a JSON object")
        if record:
            with _ecosystem_blackboard(Path.cwd()) as board:
                rows = record_parallel_conflicts(board, run_id=run_id, stage_id="cli", stage_writes=data)
        else:
            rows = detect_parallel_conflicts(data)
        payload: object = {"source": str(plan_file), "conflicts": rows}
    else:
        payload = _daemon_client(host, port).parallel_conflicts(status=status, task_id=run_id)
    if json_output:
        _print_json(payload)
        return
    rows = payload.get("conflicts", payload) if isinstance(payload, dict) else payload
    table = Table(title="muxdev parallel conflicts")
    for column in ("conflict_id", "stages", "files", "severity", "status", "resolution"):
        table.add_column(column)
    for row in rows if isinstance(rows, list) else []:
        table.add_row(*(str(row.get(column) or "") for column in ("conflict_id", "stages", "files", "severity", "status", "resolution")))
    console.print(table)


@learning_app.command("provider")
def learning_provider(
    role: Annotated[str | None, typer.Option("--role", help="Filter by role.")] = None,
    local: Annotated[bool, typer.Option("--local", help="Read/write local ecosystem DB instead of daemon.")] = False,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show persisted cross-run provider learning snapshots."""
    if local:
        with _ecosystem_blackboard(Path.cwd()) as board:
            rows = refresh_provider_learning(board, role=role)
    else:
        rows = _daemon_client(host, port).provider_learning(role=role)
    if json_output:
        _print_json(rows)
        return
    table = Table(title="muxdev provider learning")
    for column in ("provider", "role", "attempts", "successes", "failures", "human_actions", "score"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("provider", "role", "attempts", "successes", "failures", "human_actions", "score")))
    console.print(table)


@multirepo_app.command("plan")
def multirepo_plan(
    task: Annotated[str, typer.Argument(help="Task to coordinate across repositories.")],
    repo: Annotated[list[Path] | None, typer.Option("--repo", help="Repository path. Repeat for multiple repos.")] = None,
    mode: Annotated[str, typer.Option("--mode", help="design or dev.")] = "design",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Plan a multi-repo design/dev orchestration."""
    repos = repo or [Path.cwd()]
    try:
        with _ecosystem_blackboard(Path.cwd()) as board:
            payload = plan_multi_repo_orchestration(Path.cwd(), repos=repos, task=task, mode=mode, blackboard=board)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="muxdev multirepo plan"))


@multirepo_app.command("design")
def multirepo_design(
    task: Annotated[str, typer.Argument(help="Design task to coordinate across repositories.")],
    repo: Annotated[list[Path] | None, typer.Option("--repo", help="Repository path. Repeat for multiple repos.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Plan a multi-repo design orchestration."""
    multirepo_plan(task=task, repo=repo, mode="design", json_output=json_output)


@multirepo_app.command("dev")
def multirepo_dev(
    task: Annotated[str, typer.Argument(help="Dev task to coordinate across repositories.")],
    repo: Annotated[list[Path] | None, typer.Option("--repo", help="Repository path. Repeat for multiple repos.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Plan a multi-repo dev orchestration."""
    multirepo_plan(task=task, repo=repo, mode="dev", json_output=json_output)


@evidence_app.command("verify")
def evidence_verify(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Verify Evidence v2 manifest, event chain, and artifact hashes."""
    resolved, run_dir = _resolve_evidence_run(run_id)
    with _evidence_blackboard(run_dir) as blackboard:
        payload = verify_run_evidence(run_dir, resolved, blackboard)
    if json_output:
        _print_json(payload)
        return
    lines = [
        f"run_id: {payload['run_id']}",
        f"valid: {payload['valid']}",
        f"events: {payload.get('events', 0)}",
        f"artifacts: {payload.get('artifacts', 0)}",
        f"head_hash: {payload.get('head_hash') or '-'}",
    ]
    for error in payload.get("errors", []):
        lines.append(f"error: {error}")
    console.print(Panel("\n".join(lines), title="muxdev evidence verify"))


@evidence_app.command("cleanup-legacy")
def evidence_cleanup_legacy(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    yes: Annotated[bool, typer.Option("--yes", help="Confirm destructive legacy evidence cleanup.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Drop v1 evidence tables and remove v1 evidence artifacts for one run."""
    if not yes:
        raise typer.BadParameter("cleanup-legacy requires --yes")
    resolved, run_dir = _resolve_evidence_run(run_id)
    with _evidence_blackboard(run_dir) as blackboard:
        payload = cleanup_legacy_evidence(run_dir, blackboard, yes=yes)
    payload["run_id"] = resolved
    if json_output:
        _print_json(payload)
        return
    lines = [
        f"run_id: {resolved}",
        f"removed files: {len(payload.get('removed_files', []))}",
        f"dropped tables: {', '.join(payload.get('dropped_tables', []))}",
    ]
    console.print(Panel("\n".join(lines), title="muxdev evidence cleanup"))


@app.command()
def new(
    path: Annotated[Path, typer.Argument(help="Project directory to create or initialize.")],
    force: Annotated[bool, typer.Option("--force", help="Allow initializing a non-empty directory.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Create a minimal muxdev-ready project directory."""
    target = path.resolve()
    if target.exists() and any(target.iterdir()) and not force:
        raise typer.BadParameter(f"target is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    workflows = path_config(target, "runtime_root") / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    workflow_path = workflows / "software-dev.yaml"
    if force or not workflow_path.exists():
        workflow_path.write_text(SOFTWARE_DEV_WORKFLOW.strip() + "\n", encoding="utf-8")
    readme = target / "README.md"
    if force or not readme.exists():
        readme.write_text("# muxdev project\n\nRun `muxdev dev \"your task\"` from this directory.\n", encoding="utf-8")
    payload = {"path": str(target), "workflow": str(workflow_path), "status": "created"}
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="New Project"))


@app.command("continue")
def continue_task(
    task_id: Annotated[str, typer.Argument(help="Task id, or 'latest'.")] = "latest",
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", help="Budget limit for continued stages.")] = 0.5,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Continue a paused or incomplete daemon task."""
    payload = _daemon_client(host, port).continue_task(task_id, max_cost_usd=max_cost_usd)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Continue"))


@app.command()
def tasks(
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List tasks from the muxdev daemon."""
    rows = _daemon_client(host, port).tasks()
    if json_output:
        _print_json(rows)
        return
    table = Table(title="Tasks")
    for column in ("task_id", "task", "status", "current_stage", "pending_approvals", "pending_provider_actions"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("task_id", "task", "status", "current_stage", "pending_approvals", "pending_provider_actions")))
    console.print(table)


@app.command()
def status(
    task_id: Annotated[str, typer.Argument(help="Task id, or 'latest'.")] = "latest",
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show daemon task details."""
    payload = _daemon_client(host, port).task(task_id)
    if json_output:
        _print_json(payload)
        return
    console.print(status_panel(payload))


@app.command()
def stop(
    task_id: Annotated[str, typer.Argument(help="Task id to stop.")],
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Stop a daemon task."""
    payload = _daemon_client(host, port).stop_task(task_id)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Stop"))


@app.command()
def retry(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")],
    stage: Annotated[str, typer.Option("--stage", help="Stage id to reset and retry.")],
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", help="Budget limit for retried stages.")] = 0.5,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Reset one stage to pending and resume the run."""
    resolved = _resolve_run_id(run_id)
    result = SupervisorRuntime(Path.cwd()).retry(resolved, stage, max_cost_usd=max_cost_usd)
    payload = {
        "run_id": result.run_id,
        "stage": stage,
        "status": str(result.status),
        "run_dir": str(result.run_dir),
        "report": str(result.report_path) if result.report_path else None,
        "dashboard": str(_ensure_run_dashboard(result.run_id)),
    }
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Retry"))


@app.command()
def skip(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")],
    stage: Annotated[str, typer.Option("--stage", help="Stage id to mark skipped.")],
    reason: Annotated[str, typer.Option("--reason", help="Skip reason.")] = "skip requested",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Mark a stage skipped so a run can resume past it."""
    resolved = _resolve_run_id(run_id)
    run_dir = RunStore(Path.cwd()).find_run_dir(resolved)
    blackboard = Blackboard(run_dir)
    try:
        blackboard.skip_stage(resolved, stage, reason)
    finally:
        blackboard.close()
    payload = {"run_id": resolved, "stage": stage, "status": "skipped", "reason": reason, "dashboard": str(_ensure_run_dashboard(resolved))}
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Skip"))


@app.command()
def merge(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    execute: Annotated[bool, typer.Option("--execute", help="Copy worktree files back into the workspace.")] = False,
    gate_command: Annotated[str | None, typer.Option("--gate-command", help="Command to run in the run worktree before merge.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Merge a run worktree back into the current workspace; dry-run by default."""
    resolved = _resolve_run_id(run_id)
    payload = _merge_run(resolved, execute=execute, gate_command=gate_command)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Merge"))


@app.command()
def ship(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview files without copying them into the workspace.")] = False,
    gate_command: Annotated[str | None, typer.Option("--gate-command", help="Command to run in the run worktree before shipping.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Ship a run worktree back into the current workspace."""
    resolved = _resolve_run_id(run_id)
    payload = _merge_run(resolved, execute=not dry_run, gate_command=gate_command)
    payload["command"] = "ship"
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Ship"))


@app.command()
def report(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show a final report for a daemon task."""
    payload = _daemon_client(host, port).report(run_id)
    if json_output:
        _print_json(payload)
        return
    console.print(payload["content"])


@app.command()
def diff(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show the captured diff for a daemon task."""
    payload = _daemon_client(host, port).diff(run_id)
    content = payload.get("diff", "")
    if json_output:
        _print_json(payload)
        return
    console.print(content or "(empty diff)")


@app.command()
def rollback(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    to_stage: Annotated[str | None, typer.Option("--to-stage", help="Rollback worktree to the snapshot captured before this stage.")] = None,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Rollback changes inside the daemon task worktree."""
    payload = _daemon_client(host, port).rollback(run_id, to_stage=to_stage)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Rollback"))


@app.command()
def undo(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    to_stage: Annotated[str | None, typer.Option("--to-stage", help="Restore the task worktree to the snapshot captured before this stage.")] = None,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Undo a daemon task worktree back to a rollback snapshot."""
    payload = _daemon_client(host, port).rollback(run_id, to_stage=to_stage)
    payload["command"] = "undo"
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Undo"))


@app.command()
def approvals(
    status: Annotated[str | None, typer.Option("--status", help="Filter by pending/approved/denied.")] = None,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List approvals from the muxdev daemon."""
    rows = _daemon_client(host, port).approvals(status=status)
    if json_output:
        _print_json(rows)
        return
    table = Table(title="Approvals")
    for column in ("approval_id", "run_id", "stage_id", "type", "status", "reason"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("approval_id", "run_id", "stage_id", "type", "status", "reason")))
    console.print(table)


@app.command()
def actions(
    status: Annotated[str | None, typer.Option("--status", help="Filter by pending/handled/dismissed/expired.")] = "pending",
    run_id: Annotated[str | None, typer.Option("--run-id", help="Filter to one task id.")] = None,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List provider-side CLI actions that need human handling."""
    try:
        rows = _daemon_client(host, port).provider_actions(status=status, task_id=run_id)
    except DaemonConnectionError as exc:
        if _provider_actions_api_unavailable(exc):
            payload = {"provider_actions": [], "warning": _provider_actions_restart_hint()}
            if json_output:
                _print_json(payload)
                return
            console.print(Panel(payload["warning"], title="Provider Actions"))
            return
        raise
    if json_output:
        _print_json(rows)
        return
    table = Table(title="Provider Actions")
    for column in ("action_id", "run_id", "stage_id", "provider", "kind", "input_kind", "status", "prompt_text", "choices", "default_choice", "attach_command"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("action_id", "run_id", "stage_id", "provider", "kind", "input_kind", "status", "prompt_text", "choices", "default_choice", "attach_command")))
    console.print(table)


@action_app.command("handled")
def action_handled(
    action_id: Annotated[str, typer.Argument(help="Provider action id to mark handled.")],
    choice: Annotated[str | None, typer.Option("--choice", help="Record a selected choice value before marking handled.")] = None,
    text: Annotated[str | None, typer.Option("--text", help="Record a free-form response before marking handled.")] = None,
    response_json: Annotated[str | None, typer.Option("--response-json", help="Record a JSON response before marking handled.")] = None,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Mark a provider action handled after dealing with the CLI/session."""
    response = _provider_action_response(choice=choice, text=text, response_json=response_json)
    payload = _daemon_client(host, port).provider_action_handled(action_id, response=response) if response is not None else _daemon_client(host, port).provider_action_handled(action_id)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Provider Action Handled"))


@action_app.command("respond")
def action_respond(
    action_id: Annotated[str, typer.Argument(help="Provider action id to answer.")],
    choice: Annotated[str | None, typer.Option("--choice", help="Selected choice value.")] = None,
    text: Annotated[str | None, typer.Option("--text", help="Free-form response text.")] = None,
    response_json: Annotated[str | None, typer.Option("--response-json", help="Structured JSON response.")] = None,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Answer a provider action inside muxdev and mark it handled."""
    response = _provider_action_response(choice=choice, text=text, response_json=response_json)
    if response is None:
        raise typer.BadParameter("provide --choice, --text, or --response-json")
    payload = _daemon_client(host, port).provider_action_response(action_id, response)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Provider Action Response"))


@action_app.command("dismiss")
def action_dismiss(
    action_id: Annotated[str, typer.Argument(help="Provider action id to dismiss.")],
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Dismiss a provider action without treating it as muxdev approval."""
    payload = _daemon_client(host, port).provider_action_dismiss(action_id)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Provider Action Dismiss"))


@app.command()
def approve(
    approval_id: Annotated[str, typer.Argument(help="Approval id to approve.")],
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Approve a pending approval."""
    payload = _daemon_client(host, port).approve(approval_id)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Approve"))


@app.command()
def deny(
    approval_id: Annotated[str, typer.Argument(help="Approval id to deny.")],
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Deny a pending approval."""
    payload = _daemon_client(host, port).deny(approval_id)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Deny"))


@app.command()
def attach(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    agent: Annotated[str, typer.Option("--agent", help="Agent role to attach to.")] = "implementer",
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Record an attach request for a daemon task agent session."""
    payload = _daemon_client(host, port).attach_command(run_id, agent=agent)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Attach"))


@app.command()
def detach(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    agent: Annotated[str, typer.Option("--agent", help="Agent role to detach from.")] = "implementer",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Record a detach request for an agent session."""
    resolved = _resolve_run_id(run_id)
    _set_agent_session(resolved, agent, session_id=f"{resolved}:{agent}", status="detached")
    payload = {
        "run_id": resolved,
        "agent": agent,
        "session_id": f"{resolved}:{agent}",
        "status": "detached",
        "message": "agent session is marked detached",
    }
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Detach"))


@config_app.callback()
def config_main(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show the effective TOML-first config when no subcommand is provided."""
    if ctx.invoked_subcommand is None:
        config_show(json_output=json_output)
        raise typer.Exit()


@config_app.command("show")
def config_show(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show the merged TOML-first muxdev configuration."""
    payload = load_runtime_config(Path.cwd())
    legacy = load_config(Path.cwd())
    for key in ("providers", "workflows", "paths"):
        if key in legacy and key not in payload:
            payload[key] = legacy[key]
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="muxdev config"))


@config_app.command("paths")
def config_paths(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show built-in, user, project, and environment config paths."""
    payload = [source.to_dict() for source in config_sources(Path.cwd())]
    if json_output:
        _print_json(payload)
        return
    table = Table(title="Config Paths")
    for column in ("kind", "exists", "path"):
        table.add_column(column)
    for row in payload:
        table.add_row(str(row["kind"]), str(row["exists"]), str(row["path"]))
    console.print(table)


@config_app.command("source")
def config_source(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show TOML runtime config sources in merge order."""
    payload = [source.to_dict() for source in runtime_config_sources(Path.cwd())]
    if json_output:
        _print_json(payload)
        return
    table = Table(title="Runtime Config Sources")
    for column in ("kind", "exists", "path"):
        table.add_column(column)
    for row in payload:
        table.add_row(str(row["kind"]), str(row["exists"]), str(row["path"]))
    console.print(table)


@config_app.command("check")
def config_check(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Validate the TOML-first runtime config and legacy provider config."""
    payload = runtime_config_check(Path.cwd())
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Config Check"))
    if not payload["valid"]:
        raise typer.Exit(code=1)


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="Dotted key, e.g. roles.code or gate.")],
    value: Annotated[str, typer.Argument(help="Value to write.")],
    project: Annotated[bool, typer.Option("--project", help="Write project .muxdev/config.toml.")] = False,
    global_scope: Annotated[bool, typer.Option("--global", help="Write global ~/.muxdev/config.toml.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Set a TOML runtime config value."""
    payload = set_runtime_config_value(Path.cwd(), key, value, project=project and not global_scope)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{item}: {payload[item]}" for item in ("path", "key", "value")), title="Config Set"))


@config_app.command("edit")
def config_edit(
    project: Annotated[bool, typer.Option("--project", help="Show project config path.")] = False,
    global_scope: Annotated[bool, typer.Option("--global", help="Show global config path.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Print the config path to edit."""
    path = project_config_path(Path.cwd()) if project and not global_scope else global_config_path()
    payload = {"path": str(path), "exists": path.exists()}
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(f"path: {path}\nexists: {path.exists()}", title="Config Edit"))


@config_app.command("validate")
def config_validate(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Validate the merged muxdev configuration."""
    payload = validate_config(load_config(Path.cwd()))
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Config Validate"))
    if not payload["valid"]:
        raise typer.Exit(code=1)


@preset_app.command("list")
def preset_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List built-in profiles, gates, and workflows."""
    payload = {
        "profiles": sorted(PROFILES),
        "gates": sorted(GATES),
        "workflows": sorted(WORKFLOW_ALIASES),
    }
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Presets"))


@preset_app.command("show")
def preset_show(
    kind: Annotated[str, typer.Argument(help="profile, gate, or workflow.")],
    name: Annotated[str, typer.Argument(help="Preset name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show one built-in preset."""
    payload = _preset_payload(kind, name)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title=f"{kind} {name}"))


@preset_app.command("copy")
def preset_copy(
    kind: Annotated[str, typer.Argument(help="profile, gate, or workflow.")],
    name: Annotated[str, typer.Argument(help="Preset name.")],
    project: Annotated[bool, typer.Option("--project", help="Copy into project .muxdev/presets.")] = True,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Materialize one preset into the project advanced preset area."""
    payload = _write_preset(kind, name)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Preset Copy"))


@preset_app.command("edit")
def preset_edit(
    kind: Annotated[str, typer.Argument(help="profile, gate, or workflow.")],
    name: Annotated[str, typer.Argument(help="Preset name.")],
    project: Annotated[bool, typer.Option("--project", help="Use project .muxdev/presets.")] = True,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Ensure a preset file exists and print its path."""
    payload = _write_preset(kind, name)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(f"path: {payload['path']}", title="Preset Edit"))


@policy_app.command("shell")
def policy_shell(
    command: Annotated[str, typer.Argument(help="Shell command to evaluate.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Evaluate a shell command against the M4 safety policy."""
    result = SafetyPolicyEngine().evaluate_shell(command)
    payload = {"command": command, "decision": str(result.decision), "reason": result.reason}
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Policy Shell"))


@trace_app.command("view")
def trace_view(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """View a run trace in JSONL-derived table or JSON form."""
    resolved = _resolve_run_id(run_id)
    run_dir = RunStore(Path.cwd()).find_run_dir(resolved)
    rows = compact_trace(read_trace(run_dir))
    if json_output:
        _print_json({"run_id": resolved, "events": rows})
        return
    table = Table(title=f"Trace {resolved}")
    for column in ("time", "type", "stage", "data"):
        table.add_column(column)
    for row in rows:
        table.add_row(str(row["time"]), str(row["type"]), str(row["stage"]), json.dumps(row["data"], ensure_ascii=False))
    console.print(table)


@trace_app.command("chrome")
def trace_chrome(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    output: Annotated[Path | None, typer.Option("--output", help="Output Chrome trace JSON path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Export a run trace in Chrome trace-event JSON format."""
    resolved = _resolve_run_id(run_id)
    run_dir = RunStore(Path.cwd()).find_run_dir(resolved)
    events = _chrome_trace_events(read_trace(run_dir))
    output_path = output or (run_dir / "chrome_trace.json")
    output_path.write_text(json.dumps({"traceEvents": events}, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {"run_id": resolved, "path": str(output_path), "events": len(events)}
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Chrome Trace"))


@app.command()
def metrics(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    prometheus: Annotated[bool, typer.Option("--prometheus", help="Emit Prometheus text exposition.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show run usage metrics."""
    resolved = _resolve_run_id(run_id)
    run_dir = RunStore(Path.cwd()).find_run_dir(resolved)
    blackboard = Blackboard(run_dir)
    try:
        usage = blackboard.table_rows("usage_records")
        stages = blackboard.table_rows("stages")
    finally:
        blackboard.close()
    payload = {
        "run_id": resolved,
        "tokens": sum(int(row["tokens"]) for row in usage),
        "cost_usd": sum(float(row["cost_usd"]) for row in usage),
        "stages": len(stages),
        "completed_stages": sum(1 for row in stages if row["status"] == "completed"),
    }
    if prometheus:
        typer.echo(_prometheus_metrics(payload))
        return
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Metrics"))


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Text to search in the current workspace.")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum matches to return.")] = 20,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Run a lightweight local codebase retrieval search."""
    rows = _search_workspace(Path.cwd(), query, limit=limit)
    if json_output:
        _print_json(rows)
        return
    table = Table(title="Search")
    for column in ("path", "line", "text"):
        table.add_column(column)
    for row in rows:
        table.add_row(str(row["path"]), str(row["line"]), str(row["text"]))
    console.print(table)


@mcp_app.command("manifest")
def mcp_manifest(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Print the muxdev MCP server manifest."""
    payload = server_manifest(Path.cwd())
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="MCP Manifest"))


@mcp_app.command("serve")
def mcp_serve(
    request: Annotated[str | None, typer.Option("--request", help="Handle one JSON-RPC request string and exit.")] = None,
    once: Annotated[bool, typer.Option("--once", help="Read one JSON-RPC request from stdin and exit.")] = False,
    stdio: Annotated[bool, typer.Option("--stdio", help="Run a line-delimited stdio JSON-RPC loop.")] = False,
) -> None:
    """Run a minimal stdio-compatible MCP JSON-RPC surface."""
    if request is None and once:
        request = sys.stdin.read()
    if request is None and stdio:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = handle_jsonrpc(json.loads(line), Path.cwd())
            except json.JSONDecodeError as exc:
                payload = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        return
    if request is None:
        console.print("muxdev MCP stdio server is ready. Pass --stdio, --once, or --request for scripted use.")
        return
    payload = handle_jsonrpc(json.loads(request), Path.cwd())
    _print_json(payload)


@mcp_app.command("doctor")
def mcp_doctor_command(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Summarize the local muxdev MCP surface."""
    payload = mcp_doctor(Path.cwd())
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="MCP Doctor"))


@session_app.command("start")
def session_start(
    provider: Annotated[str, typer.Argument(help="Provider/session label.")],
    command: Annotated[str, typer.Option("--command", help="Command line to run as a long-lived session.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Start a long-lived provider process and record its transcript."""
    if not command:
        raise typer.BadParameter("command is required")
    args = split_command_line(command)
    record = SessionManager(Path.cwd()).start(provider, args, cwd=Path.cwd())
    payload = record.to_dict()
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Session Start"))


@session_app.command("list")
def session_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List recorded long-lived provider sessions."""
    rows = [record.to_dict() for record in SessionManager(Path.cwd()).list()]
    if json_output:
        _print_json(rows)
        return
    table = Table(title="Sessions")
    for column in ("session_id", "provider", "pid", "status", "alive", "transcript"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("session_id", "provider", "pid", "status", "alive", "transcript")))
    console.print(table)


@session_app.command("stop")
def session_stop(
    session_id: Annotated[str, typer.Argument(help="Session id to stop.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Stop a recorded long-lived provider session."""
    record = SessionManager(Path.cwd()).stop(session_id)
    payload = record.to_dict()
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Session Stop"))


@rag_app.command("index")
def rag_index(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Build a local lexical retrieval index for the current workspace."""
    payload = LocalRagIndex(Path.cwd()).build()
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="RAG Index"))


@rag_app.command("query")
def rag_query(
    query: Annotated[str, typer.Argument(help="Query text.")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum chunks to return.")] = 5,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Query the local retrieval index."""
    rows = LocalRagIndex(Path.cwd()).query(query, limit=limit)
    if json_output:
        _print_json(rows)
        return
    table = Table(title="RAG Query")
    for column in ("path", "start_line", "end_line", "score", "text"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("path", "start_line", "end_line", "score", "text")))
    console.print(table)


@graph_app.command("export")
def graph_export(
    workflow: Annotated[str, typer.Option("--workflow", help="Workflow name or YAML path.")] = "software-dev",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Export the muxdev workflow as a LangGraph-compatible node/edge graph."""
    payload = workflow_to_langgraph(workflow)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Workflow Graph"))


@deep_agent_app.command("plan")
def deep_agent_plan(
    task: Annotated[str, typer.Argument(help="Task to package for a deep-agent runtime.")],
    workflow: Annotated[str, typer.Option("--workflow", help="Workflow name or YAML path.")] = "software-dev",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Produce a Deep-Agent-compatible task pack from a muxdev workflow."""
    payload = deep_agent_task_pack(task, workflow, Path.cwd())
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Deep-Agent Task Pack"))


def _workflow_template_rows() -> list[dict[str, object]]:
    return [plugin.to_dict() for plugin in list_workflow_plugins()]


def _print_workflow_templates(rows: list[dict[str, object]], *, deprecated: bool = False) -> None:
    table = Table(title="Workflow Templates")
    for column in ("name", "phases", "supported_providers", "description"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            str(row["name"]),
            ", ".join(row["phases"]),
            ", ".join(row["supported_providers"]),
            str(row["description"]),
        )
    if deprecated:
        console.print("Deprecated: use 'muxdev workflow templates' instead.")
    console.print(table)


@workflow_app.command("templates")
def workflow_templates(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List built-in workflow templates."""
    rows = _workflow_template_rows()
    if json_output:
        _print_json(rows)
        return
    _print_workflow_templates(rows)


@workflow_app.command("plugins")
def workflow_plugins(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Deprecated alias for workflow templates."""
    rows = _workflow_template_rows()
    if json_output:
        _print_json(rows)
        return
    _print_workflow_templates(rows, deprecated=True)


@workflow_app.command("template")
def workflow_template(
    name: Annotated[str, typer.Argument(help="Workflow template name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show a workflow template definition."""
    try:
        payload = get_workflow_plugin(name).to_dict()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title=f"Workflow Template: {name}"))


@workflow_app.command("plugin")
def workflow_plugin(
    name: Annotated[str, typer.Argument(help="Workflow template name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Deprecated alias for workflow template."""
    try:
        payload = get_workflow_plugin(name).to_dict()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print("Deprecated: use 'muxdev workflow template' instead.")
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title=f"Workflow Template: {name}"))


@workflow_app.command("render")
def workflow_render(
    name: Annotated[str, typer.Argument(help="Workflow template name.")],
    phase: Annotated[str, typer.Option("--phase", help="Template phase to render.")],
    provider: Annotated[str, typer.Option("--provider", help="Provider command dialect.")],
    task: Annotated[str, typer.Option("--task", help="Task text to inject into the command.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Render a workflow template phase command for a provider dialect."""
    try:
        payload = render_plugin_command(name, phase, provider, task)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Workflow Command"))


@flow_app.command("add")
def flow_add(
    name: Annotated[str, typer.Argument(help="Flow name.")],
    task: Annotated[str, typer.Option("--task", help="Task to run when the flow is triggered.")],
    schedule: Annotated[str, typer.Option("--schedule", help="Cron-style schedule expression.")],
    provider: Annotated[str, typer.Option("--provider", help="Provider for the flow run.")] = "mock",
    workflow: Annotated[str, typer.Option("--workflow", help="Workflow name or YAML path.")] = "software-dev",
    gate_command: Annotated[str, typer.Option("--gate-command", help="Optional shell command gate to evaluate before execution.")] = "",
    disabled: Annotated[bool, typer.Option("--disabled", help="Create the flow disabled.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Add or replace a local scheduled flow definition."""
    try:
        flow = FlowRegistry(Path.cwd()).add(
            name,
            schedule=schedule,
            task=task,
            provider=provider,
            workflow=workflow,
            enabled=not disabled,
            gate_command=gate_command,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    payload = flow.to_dict()
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Flow Add"))


@flow_app.command("list")
def flow_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List local scheduled flow definitions."""
    rows = [flow.to_dict() for flow in FlowRegistry(Path.cwd()).list()]
    if json_output:
        _print_json(rows)
        return
    table = Table(title="Flows")
    for column in ("name", "schedule", "provider", "workflow", "enabled", "task"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("name", "schedule", "provider", "workflow", "enabled", "task")))
    console.print(table)


@flow_app.command("run")
def flow_run(
    name: Annotated[str, typer.Argument(help="Flow name.")],
    execute: Annotated[bool, typer.Option("--execute", help="Execute the flow now. Without this, only print the run plan.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Plan or manually execute a scheduled flow."""
    registry = FlowRegistry(Path.cwd())
    try:
        if not execute:
            payload = registry.plan_run(name)
        else:
            flow = registry.load(name)
            if not flow.enabled:
                payload = {
                    "name": flow.name,
                    "status": "disabled",
                    "task": flow.task,
                    "provider": flow.provider,
                    "workflow": flow.workflow,
                }
                if json_output:
                    _print_json(payload)
                    return
                console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Flow Run"))
                return
            if flow.gate_command:
                import subprocess

                decision = SafetyPolicyEngine().evaluate_shell(flow.gate_command)
                if decision.decision == "deny":
                    raise typer.BadParameter(f"flow gate denied by policy: {decision.reason}")
                completed = subprocess.run(
                    flow.gate_command,
                    cwd=Path.cwd(),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=True,
                    check=False,
                    **hidden_subprocess_kwargs(),
                )
                if completed.returncode != 0:
                    payload = {
                        "name": flow.name,
                        "status": "gate_failed",
                        "gate": {
                            "command": flow.gate_command,
                            "returncode": completed.returncode,
                            "stdout": completed.stdout or "",
                            "stderr": completed.stderr or "",
                        },
                    }
                    if json_output:
                        _print_json(payload)
                        return
                    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Flow Run"))
                    return
            result = SupervisorRuntime(Path.cwd()).run(flow.task, provider=flow.provider, workflow_name=flow.workflow)
            payload = {
                "name": flow.name,
                "status": str(result.status),
                "run_id": result.run_id,
                "run_dir": str(result.run_dir),
                "report": str(result.report_path) if result.report_path else None,
            }
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Flow Run"))


@skill_app.command("add")
def skill_add(
    source: Annotated[str, typer.Argument(help="Skill path, git URL placeholder, builtin:name, or new skill name.")],
    name: Annotated[str | None, typer.Option("--name", help="Override installed skill name.")] = None,
    global_scope: Annotated[bool, typer.Option("--global", help="Install into ~/.agents/skills.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Add a standard SKILL.md package to the muxdev scan path."""
    record = add_skill(Path.cwd(), source, name=name, global_scope=global_scope)
    payload = record.to_dict()
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Skill Add"))


@skill_app.command("list")
def skill_list(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List discovered muxdev skills."""
    rows = [record.to_dict() for record in scan_skills(Path.cwd(), include_disabled=True)]
    legacy = [record.to_dict() for record in SkillRegistry(Path.cwd()).list()]
    known = {row["name"] for row in rows}
    rows.extend({**row, "source": "legacy"} for row in legacy if row["name"] not in known)
    if json_output:
        _print_json(rows)
        return
    table = Table(title="Skills")
    for column in ("name", "native", "path", "description"):
        table.add_column(column)
    for row in rows:
        table.add_row(str(row["name"]), str(row.get("native", False)), str(row["path"]), str(row.get("description", "")))
    console.print(table)


@skill_app.command("catalog")
def skill_catalog(
    role: Annotated[str | None, typer.Option("--role", help="Filter by compatible role.")] = None,
    stage: Annotated[str | None, typer.Option("--stage", help="Filter by workflow stage.")] = None,
    budget: Annotated[int | None, typer.Option("--budget", help="Maximum catalog metadata characters.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Build the progressive-disclosure skill catalog without SKILL.md content."""
    payload = build_skill_catalog(Path.cwd(), role=role, stage=stage, budget=budget).to_dict()
    if json_output:
        _print_json(payload)
        return
    table = Table(title="Skill Catalog")
    for column in ("name", "trust", "roles", "source", "description"):
        table.add_column(column)
    for row in payload["skills"]:
        if isinstance(row, dict):
            table.add_row(str(row["name"]), str(row.get("trust", "")), ",".join(str(item) for item in row.get("roles", [])), str(row.get("source", "")), str(row.get("description", "")))
    console.print(table)


@skill_app.command("explain")
def skill_explain(
    task: Annotated[str, typer.Option("--task", help="Task text to score against skills.")],
    role: Annotated[list[str] | None, typer.Option("--role", help="Runtime role, repeatable.")] = None,
    stage: Annotated[str | None, typer.Option("--stage", help="Workflow stage.")] = None,
    changed_file: Annotated[list[str] | None, typer.Option("--changed-file", help="Changed file path, repeatable.")] = None,
    provider: Annotated[str, typer.Option("--provider", help="Provider adapter name.")] = "mock",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Explain role/stage/file/trust scoring for a task."""
    payload = select_skills(
        Path.cwd(),
        task=task,
        roles=role or [],
        stage=stage,
        changed_files=changed_file or [],
        provider=provider,
    ).to_dict(include_content=False)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Explain"))


@skill_app.command("activate")
def skill_activate(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    role: Annotated[str | None, typer.Option("--role", help="Activation role.")] = None,
    stage: Annotated[str | None, typer.Option("--stage", help="Activation stage.")] = None,
    provider: Annotated[str, typer.Option("--provider", help="Provider adapter name.")] = "mock",
    include_resources: Annotated[bool, typer.Option("--resources", help="Include references/assets/scripts manifests.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Load a full SKILL.md payload on demand and record activation evidence."""
    try:
        payload = activate_skill(Path.cwd(), name, role=role, stage=stage, provider=provider, include_resources=include_resources).to_dict(include_content=True)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(str(payload.get("content", "")), title=f"Skill Activate {name}"))


@skill_app.command("show")
def skill_show_command(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show a discovered skill and its SKILL.md content."""
    try:
        payload = skill_show(Path.cwd(), name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(str(payload.get("content", "")), title=f"Skill {name}"))


@skill_app.command("install")
def skill_install(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    source: Annotated[Path | None, typer.Option("--source", help="Optional file or directory to copy into the skill.")] = None,
    native: Annotated[bool, typer.Option("--native", help="Mark this skill as native-provider compatible.")] = False,
    provider: Annotated[str | None, typer.Option("--provider", help="Native provider export target.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Compatibility alias for installing into the local muxdev skill registry."""
    record = SkillRegistry(Path.cwd()).install(name, source=source, native=native, provider=provider)
    payload = record.to_dict()
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Skill Install"))


@skill_app.command("bind")
def skill_bind(
    role: Annotated[str, typer.Argument(help="Role to bind, e.g. review.")],
    skill: Annotated[str, typer.Argument(help="Skill name.")],
    project: Annotated[bool, typer.Option("--project", help="Write project skills.toml.")] = True,
    global_scope: Annotated[bool, typer.Option("--global", help="Write global skills.toml.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Bind a skill to a role in skills.toml."""
    payload = bind_skill(Path.cwd(), role, skill, project=project and not global_scope)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Skill Bind"))


@skill_app.command("unbind")
def skill_unbind(
    role: Annotated[str, typer.Argument(help="Role to unbind.")],
    skill: Annotated[str, typer.Argument(help="Skill name.")],
    project: Annotated[bool, typer.Option("--project", help="Write project skills.toml.")] = True,
    global_scope: Annotated[bool, typer.Option("--global", help="Write global skills.toml.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Remove a role-skill binding."""
    payload = unbind_skill(Path.cwd(), role, skill, project=project and not global_scope)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Skill Unbind"))


@skill_app.command("sync")
def skill_sync(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Write a discovered skill index for observability."""
    payload = sync_skills(Path.cwd())
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Skill Sync"))


@skill_app.command("lock")
def skill_lock(
    promote_memory: Annotated[bool, typer.Option("--memory/--no-memory", help="Propose skill memory items from the lock.")] = True,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Write a deterministic skill lock and optional skill memory proposals."""
    payload = write_skill_lock(Path.cwd(), promote_memory=promote_memory)
    with _ecosystem_blackboard(Path.cwd()) as board:
        for row in payload.get("skills", []):
            if isinstance(row, dict):
                board.upsert_skill_lock(
                    skill_name=str(row.get("name") or "skill"),
                    run_id=None,
                    skill_version=str(row.get("version") or "") or None,
                    skill_hash=str(row.get("skill_hash") or ""),
                    path=Path(str(row.get("path") or ".")),
                    compatible_roles=[str(item) for item in row.get("compatible_roles", []) if item] if isinstance(row.get("compatible_roles"), list) else [],
                    status="locked",
                    metadata=row,
                )
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps({"path": payload["path"], "skills": len(payload.get("skills", [])), "memory_proposals": len(payload.get("memory_proposals", []))}, ensure_ascii=False, indent=2), title="Skill Lock"))


@skill_app.command("verify")
def skill_verify(
    name: Annotated[str | None, typer.Argument(help="Optional skill name.")] = None,
    lock: Annotated[bool, typer.Option("--lock", help="Verify .muxdev/skill-lock.json.")] = True,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Verify skill integrity against skill-lock.v2."""
    payload = verify_skill_lock(Path.cwd(), name=name) if lock else skill_doctor(Path.cwd())
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Verify"))
    if not payload.get("valid", False):
        raise typer.Exit(code=1)


@skill_app.command("doctor")
def skill_doctor_command(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Diagnose skill discovery and skills.toml bindings."""
    payload = skill_doctor(Path.cwd())
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Doctor"))
    if not payload["valid"]:
        raise typer.Exit(code=1)


@skill_app.command("remove")
def skill_remove(
    name: Annotated[str, typer.Argument(help="Skill name to remove from the workspace.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Remove a workspace-owned skill directory."""
    try:
        payload = remove_skill(Path.cwd(), name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Skill Remove"))


@skill_app.command("disable")
def skill_disable(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    payload = set_skill_policy(Path.cwd(), name, disabled=True)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Disable"))


@skill_app.command("enable")
def skill_enable(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    payload = set_skill_policy(Path.cwd(), name, disabled=False)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Enable"))


@skill_app.command("trust")
def skill_trust(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    level: Annotated[str, typer.Argument(help="Trust level, e.g. project_trusted/user_trusted/org_trusted/untrusted/needs_review/quarantined.")],
    scope: Annotated[str, typer.Option("--scope", help="Trust scope: project or user.")] = "project",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    payload = set_skill_policy(Path.cwd(), name, trust=level, project=scope != "user")
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Trust"))


@skill_app.command("quarantine")
def skill_quarantine(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    reason: Annotated[str, typer.Option("--reason", help="Reason shown in policy output.")] = "",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    payload = set_skill_policy(Path.cwd(), name, trust="quarantined", auto=False)
    if reason:
        payload["reason"] = reason
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Quarantine"))


@skill_app.command("auto")
def skill_auto(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    mode: Annotated[str, typer.Argument(help="on or off.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    payload = set_skill_policy(Path.cwd(), name, auto=mode.lower() == "on")
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Auto"))


@skill_app.command("create")
def skill_create(
    name: Annotated[str, typer.Argument(help="New skill name.")],
    global_scope: Annotated[bool, typer.Option("--global", help="Create under ~/.agents/skills.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    payload = add_skill(Path.cwd(), name, global_scope=global_scope).to_dict()
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Skill Create"))


@skill_app.command("init")
def skill_init(
    name: Annotated[str, typer.Argument(help="New skill name.")],
    global_scope: Annotated[bool, typer.Option("--global", help="Create under ~/.agents/skills.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Create a standard SKILL.md package."""
    payload = add_skill(Path.cwd(), name, global_scope=global_scope).to_dict()
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Skill Init"))


@skill_app.command("validate")
def skill_validate(
    path: Annotated[Path, typer.Argument(help="Skill directory or SKILL.md path.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    payload = validate_skill_path(path)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Validate"))
    if not payload["valid"]:
        raise typer.Exit(code=1)


@skill_app.command("export")
def skill_export(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    output: Annotated[Path | None, typer.Option("--output", help="Export directory.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        payload = export_skill(Path.cwd(), name, output)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    console.print(Panel("\n".join(f"{key}: {value}" for key, value in payload.items()), title="Skill Export"))


@skill_app.command("inject")
def skill_inject(
    name: Annotated[str, typer.Argument(help="Skill name to render for prompt injection.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Print the skill text that would be injected into a provider prompt."""
    try:
        content = str(skill_show(Path.cwd(), name).get("content", ""))
    except ValueError:
        content = SkillRegistry(Path.cwd()).inject(name)
    if json_output:
        _print_json({"name": name, "content": content})
        return
    console.print(content)


@skill_app.command("eval")
def skill_eval_command(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    role: Annotated[str | None, typer.Option("--role", help="Role to evaluate.")] = None,
    provider: Annotated[str, typer.Option("--provider", help="Provider name.")] = "mock",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    payload = eval_skill(Path.cwd(), name, role=role, provider=provider)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Eval"))


@skill_app.command("score")
def skill_score_command(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    last: Annotated[str, typer.Option("--last", help="Time window label.")] = "30d",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    payload = score_skill(Path.cwd(), name, last=last)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill Score"))


@skill_app.command("abtest")
def skill_abtest_command(
    name: Annotated[str, typer.Argument(help="Skill name.")],
    versions: Annotated[str, typer.Option("--versions", help="Comma-separated versions to compare.")],
    provider: Annotated[str, typer.Option("--provider", help="Provider name.")] = "mock",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    payload = abtest_skill(Path.cwd(), name, versions=[item.strip() for item in versions.split(",") if item.strip()], provider=provider)
    if json_output:
        _print_json(payload)
        return
    console.print(Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="Skill A/B"))


@plugin_app.callback(invoke_without_command=True)
def plugin_deprecated(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Explain the removed plugin registry surface."""
    if ctx.invoked_subcommand:
        return
    payload = {
        "status": "removed",
        "message": "muxdev no longer manages plugins; use muxdev skill ... or MCP/provider config.",
    }
    if json_output:
        _print_json(payload)
        raise typer.Exit(2)
    console.print(payload["message"])
    raise typer.Exit(2)


def _removed_plugin_command(json_output: bool) -> None:
    payload = {
        "status": "removed",
        "message": "muxdev no longer manages plugins; use muxdev skill ... or MCP/provider config.",
    }
    if json_output:
        _print_json(payload)
        raise typer.Exit(2)
    console.print(payload["message"])
    raise typer.Exit(2)


@plugin_app.command("list")
def plugin_list_removed(json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False) -> None:
    """Removed: muxdev no longer manages plugins."""
    _removed_plugin_command(json_output)


@plugin_app.command("add")
def plugin_add_removed(
    source: Annotated[str, typer.Argument(help="Ignored plugin source.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Removed: muxdev no longer manages plugins."""
    _removed_plugin_command(json_output)


@plugin_app.command("validate")
def plugin_validate_removed(
    source: Annotated[str, typer.Argument(help="Ignored plugin source.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Removed: muxdev no longer validates plugin manifests."""
    _removed_plugin_command(json_output)


@plugin_app.command("show")
def plugin_show_removed(
    name: Annotated[str, typer.Argument(help="Ignored plugin name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Removed: muxdev no longer manages plugins."""
    _removed_plugin_command(json_output)


@plugin_app.command("update")
def plugin_update_removed(
    name: Annotated[str, typer.Argument(help="Ignored plugin name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Removed: muxdev no longer manages plugins."""
    _removed_plugin_command(json_output)


@plugin_app.command("remove")
def plugin_remove_removed(
    name: Annotated[str, typer.Argument(help="Ignored plugin name.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Removed: muxdev no longer manages plugins."""
    _removed_plugin_command(json_output)


@app.command()
def repl() -> None:
    """Start the interactive muxdev REPL."""
    start_repl(Path.cwd())


@app.command()
def tui(
    run_id: Annotated[str, typer.Argument(help="Run id, or 'latest'.")] = "latest",
    approve_id: Annotated[str | None, typer.Option("--approve", help="Approve an approval id before rendering.")] = None,
    deny_id: Annotated[str | None, typer.Option("--deny", help="Deny an approval id before rendering.")] = None,
    attach_agent: Annotated[str | None, typer.Option("--attach", help="Record an attach request for an agent role.")] = None,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Render a lightweight daemon-backed terminal status surface."""
    if not json_output and not any([approve_id, deny_id, attach_agent]):
        _start_daemon_tui(run_id, host=host, port=port)
        return
    actions: list[dict[str, object]] = []
    try:
        client = _daemon_client(host, port)
        if approve_id:
            actions.append(client.approve(approve_id))
        if deny_id:
            actions.append(client.deny(deny_id))
        if attach_agent:
            actions.append(client.attach_command(run_id, agent=attach_agent))
        tasks_payload = client.tasks()
        payload = client.task(run_id) if run_id != "latest" or tasks_payload else tasks_payload
        if isinstance(payload, list):
            payload = {
                "tasks": payload,
                "run": None,
                "app": {"workspace": str(Path.cwd()), "version": __version__, "providers": {"ready": [], "partial": [], "total": 0}},
                "approvals": [],
                "provider_actions": [],
            }
    except DaemonConnectionError as exc:
        if json_output:
            _print_json({"error": exc.message, "hint": _daemon_error_hint(exc)})
            return
        Console(width=120).print(_daemon_error_panel(exc))
        return
    payload["actions"] = actions
    if json_output:
        _print_json(payload)
        return
    Console(width=120).print(status_panel(payload) if payload.get("run") else Panel(json.dumps(payload, ensure_ascii=False, indent=2), title="muxdev tui"))


def _depth_override(*, simple: bool = False, safe: bool = False, deep: bool = False, parallel: bool = False) -> str | None:
    selected = [name for name, enabled in (("simple", simple), ("safe", safe), ("deep", deep), ("parallel", parallel)) if enabled]
    if len(selected) > 1:
        raise typer.BadParameter("choose only one flow depth override")
    return selected[0] if selected else None


def _task_with_design_contract(task: str | None, from_design: str | None) -> str | None:
    if not from_design:
        return task
    if from_design == "latest":
        contract_path = latest_design_contract(Path.cwd())
        if contract_path is None:
            raise typer.BadParameter("no local design contract found under .muxdev/runs")
    else:
        contract_path = Path(from_design)
    if not contract_path.exists():
        raise typer.BadParameter(f"design contract not found: {contract_path}")
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"invalid design contract JSON: {contract_path}") from exc
    base_task = task or str(contract.get("task") or "implement design contract")
    return "\n\n".join(
        [
            base_task,
            "From muxdev design contract:",
            json.dumps(
                {
                    "path": str(contract_path),
                    "run_id": contract.get("run_id"),
                    "contract_version": contract.get("contract_version"),
                    "artifacts": contract.get("artifacts", []),
                },
                ensure_ascii=False,
                indent=2,
            ),
        ]
    )


def _load_run_context(run_id: str, *, host: str, port: int) -> dict[str, object]:
    try:
        detail = _daemon_client(host, port).task(run_id)
        run = detail.get("run", {}) if isinstance(detail, dict) else {}
        return {
            "run_id": run.get("run_id", run_id) if isinstance(run, dict) else run_id,
            "context": detail.get("context", {}) if isinstance(detail, dict) else {},
        }
    except DaemonConnectionError:
        try:
            resolved = _resolve_run_id(run_id)
            run_dir = RunStore(Path.cwd()).find_run_dir(resolved)
        except (FileNotFoundError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        context_path = run_dir / "task_context.json"
        if not context_path.exists():
            return {"run_id": resolved, "context": {}}
        try:
            context = json.loads(context_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            context = {}
        return {"run_id": resolved, "context": context if isinstance(context, dict) else {}}


def _submit_main_task(
    command_workflow: str,
    *,
    task: str | None,
    profile: str | None,
    gate: str | None,
    role: list[str] | None,
    skill: list[str] | None,
    task_file: Path | None,
    provider: str | None,
    workflow: str | None,
    require_approval: str,
    max_cost_usd: float,
    host: str,
    port: int,
    json_output: bool,
    title: str,
    depth: str | None = None,
    from_design: str | None = None,
    max_review_fixes: int | None = None,
) -> None:
    try:
        task = _task_with_design_contract(task, from_design)
        request = resolve_task_request(
            workspace=Path.cwd(),
            task=task,
            command_workflow=command_workflow,
            provider=provider,
            workflow=workflow,
            profile=profile,
            gate=gate,
            depth=depth,
            role_overrides=role,
            skill_specs=skill,
            task_file=task_file,
            require_approval=_parse_csv(require_approval),
        )
        if max_review_fixes is not None:
            automation = request.get("automation", {})
            if isinstance(automation, dict):
                automation["max_review_fixes"] = max_review_fixes
        active_skills = resolve_active_skills(
            Path.cwd(),
            task=str(request["task"]),
            roles=list(request.get("runtime_roles", {}).keys()),
            provider=str(request["provider"]),
            explicit=list(request.get("skill_specs", [])),
            include_content=True,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    payload = _daemon_client(host, port).submit_task(
        {
            "task": request["task"],
            "workspace": request["workspace"],
            "provider": request["provider"],
            "workflow": request["workflow"],
            "profile": request["profile"],
            "gate": request["gate"],
            "depth": request["depth"],
            "topology": request["topology"],
            "require_approval": request["require_approval"],
            "max_cost_usd": max_cost_usd,
            "role_providers": request["role_providers"],
            "skills": active_skills,
            "ci_block_on_approval": request["ci_block_on_approval"],
            "automation": request["automation"],
        }
    )
    payload.setdefault("profile", request["profile"])
    payload.setdefault("gate", request["gate"])
    payload.setdefault("depth", request["depth"])
    payload.setdefault("topology", request["topology"])
    payload.setdefault("skills", [skill_payload.get("name") for skill_payload in active_skills])
    if json_output:
        _print_json(payload)
        return
    lines = [
        f"Task submitted: {payload['task_id']}",
        f"profile: {request['profile']}  gate: {request['gate']}  depth: {request['depth']}  topology: {request['topology']}  workflow: {request['workflow']}",
        f"provider: {request['provider']}",
        f"skills: {', '.join(str(item) for item in payload.get('skills', [])) or '-'}",
        f"Use 'muxdev status {payload['task_id']}' to track progress",
        f"Dashboard: http://{host}:{DEFAULT_UI_PORT}",
    ]
    console.print(Panel("\n".join(lines), title=title))


def _preset_payload(kind: str, name: str) -> dict[str, object]:
    kind = kind.rstrip("s")
    if kind == "profile" and name in PROFILES:
        return {"kind": "profile", "name": name, **PROFILES[name]}
    if kind == "gate" and name in GATES:
        return {"kind": "gate", "name": name, **GATES[name]}
    if kind == "workflow" and name in WORKFLOW_ALIASES:
        return {"kind": "workflow", "name": name, "workflow": WORKFLOW_ALIASES[name]}
    raise typer.BadParameter(f"unknown preset: {kind} {name}")


def _write_preset(kind: str, name: str) -> dict[str, object]:
    payload = _preset_payload(kind, name)
    kind_dir = str(payload["kind"]) + "s"
    path = Path.cwd() / ".muxdev" / "presets" / kind_dir / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_toml({key: value for key, value in payload.items() if key != "kind"}), encoding="utf-8")
    return {"kind": payload["kind"], "name": name, "path": str(path), "status": "written"}


def _ecosystem_blackboard(workspace: Path) -> Blackboard:
    root = workspace / ".muxdev"
    root.mkdir(parents=True, exist_ok=True)
    return Blackboard(root, db_path=root / "ecosystem.sqlite")


def _ensure_run_dashboard(run_id: str) -> Path:
    run_dir = RunStore(Path.cwd()).find_run_dir(run_id)
    path = dashboard_path(run_dir)
    if path.exists():
        return path.resolve()
    return write_run_dashboard(Path.cwd(), run_dir, run_id).resolve()


def _provider_actions_api_unavailable(exc: DaemonConnectionError) -> bool:
    return getattr(exc, "status_code", None) == 404 and "provider-actions" in str(getattr(exc, "path", "") or "")


def _provider_actions_restart_hint() -> str:
    return "Provider Actions API is not available on the running daemon. Run `muxdev serve --restart` to restart the daemon with the current muxdev code."


def _provider_action_response(*, choice: str | None, text: str | None, response_json: str | None) -> object | None:
    provided = [value is not None for value in (choice, text, response_json)]
    if sum(provided) > 1:
        raise typer.BadParameter("use only one of --choice, --text, or --response-json")
    if response_json is not None:
        try:
            return json.loads(response_json)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"invalid --response-json: {exc}") from exc
    if choice is not None:
        return {"choice": choice}
    if text is not None:
        return {"text": text}
    return None


def _resolve_run_id(run_id: str) -> str:
    if run_id != "latest":
        return run_id
    latest = RunStore(Path.cwd()).latest_run_id()
    if not latest:
        raise typer.BadParameter("no muxdev runs found")
    return latest


def _csv_values(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _resolve_evidence_run(run_id: str) -> tuple[str, Path]:
    local_store = RunStore(Path.cwd())
    if run_id == "latest":
        local_latest = local_store.latest_run_id()
        if local_latest:
            return local_latest, local_store.find_run_dir(local_latest)
        daemon_runs = default_daemon_paths().runs_dir
        candidates = [path for path in daemon_runs.iterdir() if path.is_dir()] if daemon_runs.exists() else []
        if candidates:
            latest = max(candidates, key=lambda path: path.stat().st_mtime)
            return latest.name, latest
        daemon_latest = _daemon_project_run_dir("latest")
        if daemon_latest:
            return daemon_latest[0], daemon_latest[1]
        raise typer.BadParameter("no muxdev runs found")
    try:
        return run_id, local_store.find_run_dir(run_id)
    except FileNotFoundError:
        daemon_dir = default_daemon_paths().runs_dir / run_id
        if daemon_dir.exists():
            return run_id, daemon_dir
        project_run = _daemon_project_run_dir(run_id)
        if project_run:
            return project_run
        raise typer.BadParameter(f"run not found: {run_id}")


def _evidence_blackboard(run_dir: Path) -> Blackboard:
    daemon_paths = default_daemon_paths()
    try:
        if daemon_paths.db_path.exists() and (
            run_dir.resolve().is_relative_to(daemon_paths.runs_dir.resolve())
            or _run_record_for_dir(run_dir) is not None
        ):
            return Blackboard(daemon_paths.data_dir, db_path=daemon_paths.db_path)
    except OSError:
        pass
    return Blackboard(run_dir)


def _daemon_project_run_dir(run_id: str) -> tuple[str, Path] | None:
    daemon_paths = default_daemon_paths()
    if not daemon_paths.db_path.exists():
        return None
    with Blackboard(daemon_paths.data_dir, db_path=daemon_paths.db_path) as board:
        rows = board.list_runs()
    if run_id == "latest":
        candidates = [(str(row.get("run_id") or ""), row) for row in rows]
    else:
        candidates = [(str(row.get("run_id") or ""), row) for row in rows if str(row.get("run_id") or "") == run_id]
    for resolved, row in candidates:
        if not resolved:
            continue
        run_dir = path_config(Path(str(row.get("workspace") or ".")), "runs") / resolved
        if run_dir.exists():
            return resolved, run_dir
    return None


def _run_record_for_dir(run_dir: Path) -> dict[str, object] | None:
    daemon_paths = default_daemon_paths()
    if not daemon_paths.db_path.exists():
        return None
    try:
        target = run_dir.resolve()
    except OSError:
        return None
    with Blackboard(daemon_paths.data_dir, db_path=daemon_paths.db_path) as board:
        for row in board.list_runs():
            run_id = str(row.get("run_id") or "")
            candidate = path_config(Path(str(row.get("workspace") or ".")), "runs") / run_id
            try:
                if candidate.resolve() == target:
                    return row
            except OSError:
                continue
    return None


def _iter_run_blackboards() -> list[tuple[str, Path, Blackboard]]:
    store = RunStore(Path.cwd())
    if not store.runs_dir.exists():
        return []
    result: list[tuple[str, Path, Blackboard]] = []
    for run_dir in store.runs_dir.iterdir():
        if run_dir.is_dir() and (run_dir / "blackboard.sqlite").exists():
            try:
                result.append((run_dir.name, run_dir, Blackboard(run_dir)))
            except Exception:
                continue
    return result


def _list_all_approvals(status: str | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for _, _, blackboard in _iter_run_blackboards():
        try:
            rows.extend(blackboard.list_approvals(status=status))
        finally:
            blackboard.close()
    return rows


def _decide_approval(approval_id: str, status: ApprovalStatus) -> dict[str, object]:
    for run_id, _, blackboard in _iter_run_blackboards():
        try:
            rows = blackboard.list_approvals(run_id=run_id)
            if any(row["approval_id"] == approval_id for row in rows):
                blackboard.decide_approval(approval_id, status)
                return {"approval_id": approval_id, "status": str(status), "run_id": run_id}
        finally:
            blackboard.close()
    raise typer.BadParameter(f"approval not found: {approval_id}")


def _set_agent_session(run_id: str, agent: str, *, session_id: str, status: str) -> None:
    run_dir = RunStore(Path.cwd()).find_run_dir(run_id)
    blackboard = Blackboard(run_dir)
    try:
        run = blackboard.get_run(run_id)
        blackboard.upsert_agent(run_id, agent, str(run["provider"]), session_id=session_id, status=status)
    finally:
        blackboard.close()


def _terminal_handoff(run_id: str, agent: str) -> dict[str, object]:
    tmux = TmuxBackend()
    session_name = f"muxdev-{run_id}-{agent}".replace(":", "-").replace("_", "-")
    if tmux.available:
        return {"mode": "tmux", "command": tmux.attach_command(session_name), "session": session_name}
    run_dir = RunStore(Path.cwd()).find_run_dir(run_id)
    transcript_candidates = sorted((run_dir / "session").glob(f"*{agent}*.log")) if (run_dir / "session").exists() else []
    transcript = transcript_candidates[-1] if transcript_candidates else run_dir / "trace.jsonl"
    return {"mode": "transcript", "command": follow_file_command(transcript), "path": str(transcript)}


def _clean_worktree_without_git(worktree: Path) -> list[str]:
    resolved = worktree.resolve()
    cleaned: list[str] = []
    for child in worktree.iterdir():
        if child.name == ".git":
            continue
        child_resolved = child.resolve()
        if resolved not in child_resolved.parents and child_resolved != resolved:
            raise RuntimeError(f"refusing to clean path outside worktree: {child}")
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            cleaned.append(str(child.relative_to(worktree)))
        else:
            try:
                child.unlink(missing_ok=True)
                cleaned.append(str(child.relative_to(worktree)))
            except PermissionError:
                child.write_text("", encoding="utf-8")
                cleaned.append(f"{child.relative_to(worktree)} (truncated)")
    return cleaned


def _merge_run(run_id: str, *, execute: bool, gate_command: str | None = None) -> dict[str, object]:
    import subprocess

    workspace = Path.cwd().resolve()
    run_dir = RunStore(workspace).find_run_dir(run_id)
    blackboard = Blackboard(run_dir)
    try:
        run = blackboard.get_run(run_id)
        worktree = Path(run["worktree"]).resolve()
    finally:
        blackboard.close()
    if not worktree.exists():
        raise typer.BadParameter(f"worktree not found: {worktree}")
    gate: dict[str, object] | None = None
    if gate_command:
        decision = SafetyPolicyEngine().evaluate_shell(gate_command)
        if decision.decision == "deny":
            raise typer.BadParameter(f"merge gate denied by policy: {decision.reason}")
        completed = subprocess.run(
            gate_command,
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        gate = {
            "command": gate_command,
            "returncode": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
        }
        if completed.returncode != 0:
            return {
                "run_id": run_id,
                "worktree": str(worktree),
                "execute": execute,
                "status": "gate_failed",
                "gate": gate,
                "files": [],
            }
    planned: list[str] = []
    for path in worktree.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(worktree)
        if ".git" in rel.parts or ".muxdev" in rel.parts:
            continue
        destination = (workspace / rel).resolve()
        if workspace not in destination.parents and destination != workspace:
            raise RuntimeError(f"refusing to merge outside workspace: {destination}")
        planned.append(rel.as_posix())
        if execute:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    return {
        "run_id": run_id,
        "worktree": str(worktree),
        "execute": execute,
        "status": "merged" if execute else "dry_run",
        "gate": gate,
        "files": planned,
    }


def _chrome_trace_events(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        events.append(
            {
                "name": str(row.get("type", "event")),
                "cat": "muxdev",
                "ph": "i",
                "ts": index * 1000,
                "pid": 1,
                "tid": str(row.get("stage") or "run"),
                "s": "t",
                "args": row.get("data", {}),
            }
        )
    return events


def _prometheus_metrics(payload: dict[str, object]) -> str:
    run_id = str(payload["run_id"]).replace("\\", "\\\\").replace('"', '\\"')
    return "\n".join(
        [
            "# HELP muxdev_run_tokens Total tokens recorded for a muxdev run.",
            "# TYPE muxdev_run_tokens counter",
            f'muxdev_run_tokens{{run_id="{run_id}"}} {payload["tokens"]}',
            "# HELP muxdev_run_cost_usd Total estimated cost recorded for a muxdev run.",
            "# TYPE muxdev_run_cost_usd counter",
            f'muxdev_run_cost_usd{{run_id="{run_id}"}} {payload["cost_usd"]}',
            "# HELP muxdev_run_completed_stages Completed stages in a muxdev run.",
            "# TYPE muxdev_run_completed_stages gauge",
            f'muxdev_run_completed_stages{{run_id="{run_id}"}} {payload["completed_stages"]}',
        ]
    )


def _search_workspace(workspace: Path, query: str, *, limit: int) -> list[dict[str, object]]:
    runtime_root = str(load_config(workspace).get("paths", {}).get("runtime_root", ".muxdev"))
    ignored_dirs = {".git", runtime_root, ".muxdev", ".pytest_cache", "__pycache__"}
    rows: list[dict[str, object]] = []
    needle = query.lower()
    for path in workspace.rglob("*"):
        if len(rows) >= limit:
            break
        if not path.is_file() or any(part in ignored_dirs for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            if needle in line.lower():
                rows.append({"path": path.relative_to(workspace).as_posix(), "line": number, "text": line.strip()})
                if len(rows) >= limit:
                    break
    return rows
