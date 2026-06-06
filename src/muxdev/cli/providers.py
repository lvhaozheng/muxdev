"""Provider CLI subcommands."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from ..providers import detect_providers, probe_provider
from ..ui.render import provider_table
from .common import _account_command, _install_provider_command, _print_json


provider_app = typer.Typer(help="Provider discovery and diagnostics")
console = Console(width=320)


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
