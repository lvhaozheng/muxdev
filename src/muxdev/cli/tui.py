"""Daemon-backed conversational TUI helpers for muxdev."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from .. import __version__
from ..clients.daemon import DaemonClient, DaemonConnectionError
from ..config.runtime import resolve_task_request
from ..daemon.paths import DEFAULT_API_PORT, DEFAULT_HOST, DEFAULT_UI_PORT
from ..daemon.process import start_daemon
from ..services.skill_engine import resolve_active_skills
from ..ui.tui import (
    daemon_approvals_text,
    daemon_chat_view,
    daemon_diff_text,
    daemon_help_text,
    daemon_provider_actions_text,
    daemon_report_text,
    daemon_task_detail_text,
    daemon_tasks_text,
)


def _daemon_client(host: str = DEFAULT_HOST, port: int = DEFAULT_API_PORT) -> DaemonClient:
    return DaemonClient(host=host, port=port)


def _render_daemon_tui(run_id: str = "latest", *, host: str = DEFAULT_HOST, port: int = DEFAULT_API_PORT) -> None:
    console_tui = Console(width=120)
    try:
        snapshot = _daemon_tui_snapshot(run_id, host=host, port=port)
        console_tui.print(
            daemon_chat_view(
                workspace=Path.cwd(),
                version=__version__,
                host=host,
                api_port=port,
                ui_port=DEFAULT_UI_PORT,
                daemon=snapshot["daemon"],
                tasks=snapshot["tasks"],
                task_payload=snapshot["task_payload"],
                approvals=snapshot["approvals"],
                provider_actions=snapshot["provider_actions"],
            )
        )
    except DaemonConnectionError as exc:
        console_tui.print(_daemon_error_panel(exc))


def _start_daemon_tui(run_id: str = "latest", *, host: str = DEFAULT_HOST, port: int = DEFAULT_API_PORT) -> None:
    """Start the daemon-backed interactive TUI, or render once for pipes/tests."""
    if not sys.stdin.isatty():
        _render_daemon_tui(run_id, host=host, port=port)
        return

    console_tui = Console(width=120)
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
    except Exception:
        _render_daemon_tui(run_id, host=host, port=port)
        console_tui.print("prompt_toolkit is unavailable; rendered dashboard once.")
        return

    commands = {
        "/help": "Show commands",
        "/refresh": "Refresh the task board",
        "/tasks": "List daemon tasks",
        "/run": "Submit a dev task: /run <task>",
        "/dev": "Submit a dev task: /dev [options] <task>",
        "/design": "Submit a design task: /design [options] <task>",
        "/fix": "Submit a fix task: /fix [options] <task>",
        "/review": "Submit a review task: /review [options] <task>",
        "/test": "Submit a test task: /test [options] <task>",
        "/status": "Show task detail: /status [task-id]",
        "/continue": "Continue a task: /continue [task-id]",
        "/stop": "Stop a task: /stop [task-id]",
        "/approve": "Approve an item: /approve <approval-id>",
        "/deny": "Deny an item: /deny <approval-id>",
        "/approvals": "List pending approvals",
        "/actions": "List pending provider actions",
        "/action": "Update provider action: /action handled <id>",
        "/parallel": "List open parallel-squad conflicts",
        "/learning": "Show provider learning snapshots",
        "/report": "Show report: /report [task-id]",
        "/diff": "Show diff: /diff [task-id]",
        "/dashboard": "Print Dashboard URL",
        "/start": "Start daemon in the background",
        "/quit": "Exit TUI",
    }
    session = PromptSession(
        completer=WordCompleter(list(commands), meta_dict=commands, ignore_case=True, sentence=True),
        complete_while_typing=True,
    )
    current_task = run_id
    message = ""
    last_command = ""
    first_render = True
    while True:
        _render_daemon_tui_frame(console_tui, current_task, message=message, command=last_command, host=host, port=port, clear_screen=first_render)
        first_render = False
        line = session.prompt("muxdev › ").strip()
        if not line:
            message = ""
            last_command = ""
            continue
        if line in {"/quit", "quit", "q", "exit"}:
            break
        message, selected = _handle_daemon_tui_command(line, current_task, host=host, port=port, commands=commands)
        last_command = line
        if selected:
            current_task = selected


def _render_daemon_tui_frame(
    console_tui: Console,
    run_id: str,
    *,
    message: str = "",
    command: str = "",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_API_PORT,
    clear_screen: bool = False,
) -> None:
    if clear_screen:
        console_tui.clear()
    try:
        snapshot = _daemon_tui_snapshot(run_id, host=host, port=port)
        console_tui.print(
            daemon_chat_view(
                workspace=Path.cwd(),
                version=__version__,
                host=host,
                api_port=port,
                ui_port=DEFAULT_UI_PORT,
                daemon=snapshot["daemon"],
                tasks=snapshot["tasks"],
                task_payload=snapshot["task_payload"],
                approvals=snapshot["approvals"],
                provider_actions=snapshot["provider_actions"],
                command=command,
                message=message,
            )
        )
    except DaemonConnectionError as exc:
        console_tui.print(_daemon_error_panel(exc))


def _daemon_tui_snapshot(run_id: str, *, host: str, port: int) -> dict[str, object]:
    client = _daemon_client(host, port)
    daemon = client.health()
    tasks = client.tasks()
    approvals = client.approvals(status="pending")
    warnings: list[str] = []
    try:
        provider_actions = client.provider_actions(status="pending")
    except DaemonConnectionError as exc:
        if _provider_actions_api_missing(exc):
            provider_actions = []
            warnings.append(_provider_actions_restart_hint())
        else:
            raise
    if warnings:
        daemon = {**daemon, "warnings": warnings}
    task_payload = None
    if tasks:
        task_payload = client.task(run_id)
    return {"daemon": daemon, "tasks": tasks, "approvals": approvals, "provider_actions": provider_actions, "task_payload": task_payload}


def _handle_daemon_tui_command(
    line: str,
    current_task: str,
    *,
    host: str,
    port: int,
    commands: dict[str, str],
) -> tuple[str, str | None]:
    text = line.strip()
    if text == "/help":
        return daemon_help_text(), None
    if text == "/refresh":
        return "refreshed", None
    if text == "/start":
        payload = start_daemon(host=host, api_port=port, ui_port=DEFAULT_UI_PORT)
        return "\n".join(f"{key}: {value}" for key, value in payload.items()), None
    if text == "/dashboard":
        return f"Dashboard: http://{host}:{DEFAULT_UI_PORT}\nAPI: http://{host}:{port}", None

    try:
        client = _daemon_client(host, port)
        if text == "/tasks":
            rows = client.tasks()
            return daemon_tasks_text(rows), None
        submit = _parse_submit_command(text)
        if submit:
            payload, description = _submit_tui_task(client, submit)
            task_id = str(payload["task_id"])
            return f"submitted {task_id}\n{description}\nUse /status {task_id} or open /dashboard.", task_id
        if text and not text.startswith("/"):
            payload, description = _submit_tui_task(
                client,
                {
                    "command_workflow": "dev",
                    "task": text,
                    "role": [],
                    "skill": [],
                    "require_approval": set(),
                    "max_cost_usd": 0.5,
                },
            )
            task_id = str(payload["task_id"])
            return f"submitted {task_id}\n{description}\nUse /status {task_id} or open /dashboard.", task_id
        if text == "/status" or text.startswith("/status "):
            task_id = text.split(maxsplit=1)[1] if " " in text else current_task
            payload = client.task(task_id)
            run = payload.get("run", {})
            return daemon_task_detail_text(payload), str(run.get("run_id") or task_id)
        if text == "/continue" or text.startswith("/continue "):
            task_id = text.split(maxsplit=1)[1] if " " in text else current_task
            payload = client.continue_task(task_id)
            return f"{payload.get('task_id')}: {payload.get('status')}", str(payload.get("task_id") or task_id)
        if text == "/stop" or text.startswith("/stop "):
            task_id = text.split(maxsplit=1)[1] if " " in text else current_task
            payload = client.stop_task(task_id)
            return f"{payload.get('task_id')}: {payload.get('status')}", str(payload.get("task_id") or task_id)
        if text == "/approvals":
            rows = client.approvals(status="pending")
            return daemon_approvals_text(rows), None
        if text == "/actions":
            try:
                rows = client.provider_actions(status="pending")
            except DaemonConnectionError as exc:
                if _provider_actions_api_missing(exc):
                    return _provider_actions_restart_hint(), None
                raise
            return daemon_provider_actions_text(rows), None
        if text == "/parallel":
            rows = client.parallel_conflicts(status="open")
            if not rows:
                return "No open parallel-squad conflicts.", None
            return "\n".join(f"{row.get('conflict_id')} {row.get('severity')} {row.get('status')} {row.get('files') or row.get('files_json')}" for row in rows), None
        if text == "/learning":
            rows = client.provider_learning()
            if not rows:
                return "No provider learning snapshots yet.", None
            return "\n".join(f"{row.get('provider')}/{row.get('role')}: score={row.get('score')} attempts={row.get('attempts')}" for row in rows), None
        if text.startswith("/action handled "):
            payload = client.provider_action_handled(text.split(maxsplit=2)[2])
            return f"{payload.get('action_id')}: {payload.get('status')}", str(payload.get("run_id") or current_task)
        if text.startswith("/action dismiss "):
            payload = client.provider_action_dismiss(text.split(maxsplit=2)[2])
            return f"{payload.get('action_id')}: {payload.get('status')}", str(payload.get("run_id") or current_task)
        if text.startswith("/approve "):
            payload = client.approve(text.split(maxsplit=1)[1])
            return f"{payload.get('approval_id')}: {payload.get('status')}", str(payload.get("run_id") or current_task)
        if text.startswith("/deny "):
            payload = client.deny(text.split(maxsplit=1)[1])
            return f"{payload.get('approval_id')}: {payload.get('status')}", str(payload.get("run_id") or current_task)
        if text == "/report" or text.startswith("/report "):
            task_id = text.split(maxsplit=1)[1] if " " in text else current_task
            payload = client.report(task_id)
            resolved = str(payload.get("task_id") or task_id)
            return daemon_report_text(payload, resolved), resolved
        if text == "/diff" or text.startswith("/diff "):
            task_id = text.split(maxsplit=1)[1] if " " in text else current_task
            payload = client.diff(task_id)
            resolved = str(payload.get("task_id") or task_id)
            return daemon_diff_text(payload, resolved), resolved
    except DaemonConnectionError as exc:
        return f"{exc.message}\n{_daemon_error_hint(exc)}", None
    return "unknown command; type /help", None


def _daemon_error_hint(exc: DaemonConnectionError) -> str:
    if getattr(exc, "suggest_start", False):
        return "Run `muxdev start` to launch the local service."
    if _provider_actions_api_missing(exc):
        return _provider_actions_restart_hint()
    return "The daemon answered but the request failed. Check `muxdev serve --status` and the daemon log."


def _daemon_error_panel(exc: DaemonConnectionError) -> Panel:
    return Panel(f"{exc.message}\n{_daemon_error_hint(exc)}", title="muxdev daemon")


def _provider_actions_api_missing(exc: DaemonConnectionError) -> bool:
    return getattr(exc, "status_code", None) == 404 and "provider-actions" in str(getattr(exc, "path", "") or "")


def _provider_actions_restart_hint() -> str:
    return "Provider Actions API is not available on the running daemon. Run `muxdev serve --restart` to restart the daemon with the current muxdev code."


def _parse_submit_command(text: str) -> dict[str, Any] | None:
    if not text.startswith(("/run", "/dev", "/design", "/fix", "/review", "/test")):
        return None
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        return {"error": str(exc)}
    if not parts:
        return None
    command = parts[0].lstrip("/")
    workflow = "dev" if command == "run" else command
    options: dict[str, Any] = {
        "command_workflow": workflow,
        "task_parts": [],
        "role": [],
        "skill": [],
        "require_approval": set(),
        "max_cost_usd": 0.5,
    }
    index = 1
    while index < len(parts):
        token = parts[index]
        if token in {"--provider", "--profile", "-p", "--gate", "-g", "--workflow", "--max-cost-usd", "--role", "--skill", "-s", "--require-approval"}:
            if index + 1 >= len(parts):
                return {"error": f"missing value for {token}"}
            value = parts[index + 1]
            if token == "--provider":
                options["provider"] = value
            elif token in {"--profile", "-p"}:
                options["profile"] = value
            elif token in {"--gate", "-g"}:
                options["gate"] = value
            elif token == "--workflow":
                options["workflow"] = value
            elif token == "--max-cost-usd":
                try:
                    options["max_cost_usd"] = float(value)
                except ValueError:
                    return {"error": f"invalid --max-cost-usd value: {value}"}
            elif token == "--role":
                options["role"].append(value)
            elif token in {"--skill", "-s"}:
                options["skill"].append(value)
            elif token == "--require-approval":
                options["require_approval"].update(item.strip() for item in value.split(",") if item.strip())
            index += 2
            continue
        if token in {"--simple", "--safe", "--deep", "--parallel"}:
            depth = token.removeprefix("--")
            if options.get("depth") and options["depth"] != depth:
                return {"error": "choose only one depth option: --simple/--safe/--deep/--parallel"}
            options["depth"] = depth
            index += 1
            continue
        options["task_parts"].append(token)
        index += 1
    options["task"] = " ".join(str(item) for item in options.pop("task_parts")).strip() or None
    return options


def _submit_tui_task(client: DaemonClient, submit: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if submit.get("error"):
        raise DaemonConnectionError(str(submit["error"]))
    try:
        request = resolve_task_request(
            workspace=Path.cwd(),
            task=submit.get("task"),
            command_workflow=str(submit["command_workflow"]),
            provider=submit.get("provider"),
            workflow=submit.get("workflow"),
            profile=submit.get("profile"),
            gate=submit.get("gate"),
            depth=submit.get("depth"),
            role_overrides=list(submit.get("role") or []),
            skill_specs=list(submit.get("skill") or []),
            require_approval=set(submit.get("require_approval") or set()),
        )
        active_skills = resolve_active_skills(
            Path.cwd(),
            task=str(request["task"]),
            roles=list(request.get("runtime_roles", {}).keys()),
            provider=str(request["provider"]),
            explicit=list(request.get("skill_specs", [])),
        )
    except ValueError as exc:
        raise DaemonConnectionError(str(exc)) from exc
    payload = client.submit_task(
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
            "max_cost_usd": float(submit.get("max_cost_usd", 0.5)),
            "role_providers": request["role_providers"],
            "skills": active_skills,
            "ci_block_on_approval": request["ci_block_on_approval"],
            "automation": request["automation"],
        }
    )
    description = (
        f"workflow={request['workflow']} profile={request['profile']} gate={request['gate']} "
        f"depth={request['depth']} provider={request['provider']} skills={', '.join(str(skill.get('name')) for skill in active_skills) or '-'}"
    )
    return payload, description
