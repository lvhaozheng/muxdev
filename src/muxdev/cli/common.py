"""Shared CLI formatting and daemon client helpers."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel

from ..clients.daemon import DaemonClient
from ..config.accounts import AccountInfo, get_account_info
from ..config.installers import InstallResult, install_provider
from ..daemon.paths import DEFAULT_API_PORT, DEFAULT_HOST, DEFAULT_UI_PORT

console = Console(width=320)


def _print_json(payload: object) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _daemon_client(host: str = DEFAULT_HOST, port: int = DEFAULT_API_PORT) -> DaemonClient:
    return DaemonClient(host=host, port=port)


def _print_service_started(payload: dict[str, object]) -> None:
    lines = [
        "muxdev service status",
        f"Dashboard: {payload.get('dashboard_url') or payload.get('dashboard') or f'http://{DEFAULT_HOST}:{DEFAULT_UI_PORT}'}",
        f"API:       {payload.get('api_url') or f'http://{DEFAULT_HOST}:{DEFAULT_API_PORT}'}",
        f"PID:       {payload.get('pid') or '-'}",
        f"Logs:      {payload.get('log') or '-'}",
    ]
    console.print(Panel("\n".join(lines), title="muxdev serve"))


def _account_command(name: str, *, json_output: bool) -> None:
    try:
        info = get_account_info(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if json_output:
        _print_json(info.to_dict())
        return
    console.print(_account_panel(info))


def _install_provider_command(name: str, *, execute: bool, json_output: bool) -> None:
    try:
        result = install_provider(name, execute=execute)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if json_output:
        _print_json(result.to_dict())
        return

    console.print(_install_result_panel(result))
    if result.status == "failed":
        raise typer.Exit(code=1)


def _install_result_panel(result: InstallResult) -> Panel:
    plan = result.plan
    lines = [
        f"provider: {result.provider}",
        f"status: {result.status}",
        f"execute: {'yes' if result.executed else 'no'}",
        f"manager: {plan.manager}",
    ]
    if plan.command:
        lines.append(f"install: {' '.join(plan.command)}")
    if plan.verify_command:
        lines.append(f"verify: {' '.join(plan.verify_command)}")
    if plan.docs_url:
        lines.append(f"docs: {plan.docs_url}")
    if plan.account.required:
        lines.append(f"signup: {plan.account.signup_url}")
        lines.append(f"login: {plan.account.login_command or 'follow provider UI'}")
    if result.error:
        lines.append(f"error: {result.error}")
    elif plan.notes:
        lines.append(f"notes: {plan.notes}")
    if not result.executed and plan.supported:
        lines.append("run with --execute to install")
    return Panel("\n".join(lines), title="Provider Install")


def _account_panel(info: AccountInfo) -> Panel:
    lines = [
        f"provider: {info.provider}",
        f"account required: {'yes' if info.required else 'no'}",
    ]
    if info.signup_url:
        lines.append(f"signup: {info.signup_url}")
    if info.login_command:
        lines.append(f"login: {info.login_command}")
    elif info.required:
        lines.append("login: follow provider UI")
    if info.docs_url:
        lines.append(f"docs: {info.docs_url}")
    if info.notes:
        lines.append(f"notes: {info.notes}")
    return Panel("\n".join(lines), title="Provider Account")


def _parse_csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _role_providers(**roles: str | None) -> dict[str, str]:
    return {role: provider for role, provider in roles.items() if provider}
