"""Daemon-backed conversational TUI helpers for muxdev."""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .. import __version__
from ..clients.daemon import DaemonClient, DaemonConnectionError
from ..daemon.paths import DEFAULT_API_PORT, DEFAULT_HOST, DEFAULT_UI_PORT
from ..daemon.process import start_daemon
from ..ui.tui import (
    daemon_approvals_text,
    daemon_chat_view,
    daemon_diff_text,
    daemon_help_text,
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
        "/run": "Submit a task: /run <task>",
        "/status": "Show task detail: /status [task-id]",
        "/continue": "Continue a task: /continue [task-id]",
        "/stop": "Stop a task: /stop [task-id]",
        "/approve": "Approve an item: /approve <approval-id>",
        "/deny": "Deny an item: /deny <approval-id>",
        "/approvals": "List pending approvals",
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
    task_payload = None
    if tasks:
        task_payload = client.task(run_id)
    return {"daemon": daemon, "tasks": tasks, "approvals": approvals, "task_payload": task_payload}


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
        if text.startswith("/run "):
            payload = client.submit_task({"task": text.split(maxsplit=1)[1], "workspace": str(Path.cwd()), "provider": "mock"})
            task_id = str(payload["task_id"])
            return f"submitted {task_id}\nUse /status {task_id} or open /dashboard.", task_id
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
    return "The daemon answered but the request failed. Check `muxdev serve --status` and the daemon log."


def _daemon_error_panel(exc: DaemonConnectionError) -> Panel:
    return Panel(f"{exc.message}\n{_daemon_error_hint(exc)}", title="muxdev daemon")
