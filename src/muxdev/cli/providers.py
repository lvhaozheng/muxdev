"""Provider CLI subcommands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ..daemon.paths import DEFAULT_API_PORT, DEFAULT_HOST
from ..providers import detect_providers, probe_provider
from ..services.provider_certification import certify_adapter, list_certifications
from ..services.product_experience import build_provider_setup_wizard
from ..ui.render import provider_table
from .common import _account_command, _daemon_client, _install_provider_command, _print_json


provider_app = typer.Typer(help="Provider discovery and diagnostics")
console = Console(width=320)


@provider_app.command("certify")
def provider_certify(
    name: Annotated[str, typer.Argument(help="Certified Adapter name (codex, qwen, mock, replay).")],
    offline: Annotated[bool, typer.Option("--offline", help="Run no-model, no-network certification (default).")] = False,
    live: Annotated[bool, typer.Option("--live", help="Run a bounded real Provider smoke test.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Acknowledge that live certification can spend model budget.")] = False,
    max_cost_usd: Annotated[float | None, typer.Option("--max-cost-usd", help="Required upper budget for live certification.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Certify one Adapter; live model calls are never implicit."""
    if offline and live:
        raise typer.BadParameter("choose only one of --offline or --live")
    try:
        payload = certify_adapter(name, live=live, acknowledged=yes, max_cost_usd=max_cost_usd)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if json_output:
        _print_json(payload)
        return
    table = Table(title=f"Adapter Certification: {name}")
    for column in ("provider", "status", "mode", "provider_version", "adapter_version", "trust_tier", "issued_at", "expires_at"):
        table.add_column(column)
    table.add_row(*(str(payload.get(column) or "") for column in ("provider", "status", "mode", "provider_version", "adapter_version", "trust_tier", "issued_at", "expires_at")))
    console.print(table)


@provider_app.command("certification")
def provider_certification(
    name: Annotated[str | None, typer.Argument(help="Optional Provider name.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Show persisted Adapter certification reports without credential details."""
    rows = list_certifications(name)
    if json_output:
        _print_json(rows)
        return
    table = Table(title="Adapter Certifications")
    for column in ("provider", "status", "mode", "provider_version", "adapter_version", "trust_tier", "issued_at"):
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column) or "") for column in ("provider", "status", "mode", "provider_version", "adapter_version", "trust_tier", "issued_at")))
    console.print(table)


@provider_app.command("detect")
def provider_detect(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Detect all known providers and print the M0 capability matrix."""
    probes = detect_providers()
    if json_output:
        _print_json([probe.to_dict() for probe in probes])
        return
    console.print(provider_table(probes))


@provider_app.command("list")
def provider_list(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """List all known providers and their current status."""
    provider_detect(json_output=json_output)


@provider_app.command("setup")
def provider_setup(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Show the provider setup wizard with install, login, and doctor steps."""
    probes = detect_providers()
    payload = build_provider_setup_wizard(Path.cwd(), probes=probes)
    if json_output:
        _print_json(payload)
        return
    table = Table(title="Provider Setup Wizard")
    for column in ("provider", "status", "installed", "action"):
        table.add_column(column)
    for row in payload["steps"]:
        table.add_row(str(row["provider"]), str(row["status"]), str(row["installed"]), str(row["action"]))
    console.print(table)


@provider_app.command("doctor")
def provider_doctor(
    name: Annotated[str, typer.Argument(help="Provider name to inspect.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Inspect one provider using safe static probes."""
    try:
        probe = probe_provider(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if json_output:
        _print_json(probe.to_dict())
        return
    console.print(provider_table([probe]))


@provider_app.command("score")
def provider_score(
    role: Annotated[str | None, typer.Option("--role", help="Filter scores to one role.")] = None,
    host: Annotated[str, typer.Option("--host", help="Daemon API host.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", help="Daemon API port.")] = DEFAULT_API_PORT,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Show provider reliability scores from recorded runtime attempts."""
    rows = _daemon_client(host, port).provider_scores(role=role)
    if json_output:
        _print_json(rows)
        return
    table = Table(title="Provider Scores")
    for column in ("provider", "role", "score", "attempts", "successes", "failures", "retries", "human_actions", "last_failure_kind"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            *(
                str(row.get(column) if row.get(column) is not None else "")
                for column in ("provider", "role", "score", "attempts", "successes", "failures", "retries", "human_actions", "last_failure_kind")
            )
        )
    console.print(table)


@provider_app.command("account")
def provider_account(
    name: Annotated[str, typer.Argument(help="Provider account/login information to show.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Show account signup and login information for a provider."""
    _account_command(name, json_output=json_output)


@provider_app.command("install")
def provider_install(
    name: Annotated[str, typer.Argument(help="Provider CLI to install.")],
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Run the install command. Without this, only print the plan."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Install a supported provider CLI, dry-run by default."""
    _install_provider_command(name, execute=execute, json_output=json_output)
