"""Pure renderers for the daemon-backed terminal interface.

The renderers accept already-fetched dictionaries and never access providers,
the runtime, or SQLite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


COMMAND_GROUPS: dict[str, list[tuple[str, str]]] = {
    "Work": [
        ("/dev <task>", "submit a development task"),
        ("/design <task>", "submit a design task"),
        ("/fix <task>", "submit a fix task"),
        ("/review [task]", "submit a review task"),
        ("/test [task]", "submit a test task"),
        ("/run <task>", "alias for /dev"),
        ("/continue [id]", "continue a paused task"),
        ("/recover [id]", "recover a blocked task"),
        ("/stop <id>", "abort a task"),
        ("/status [id]", "show task detail"),
        ("/tasks", "list recent tasks"),
    ],
    "Review": [
        ("/approvals", "list pending approvals"),
        ("/approve <id>", "approve an item"),
        ("/deny <id>", "deny an item"),
        ("/feedback <id> <text>", "answer a planning question"),
    ],
    "Provider Actions": [
        ("/actions", "list pending provider actions"),
        ("/action handled <id>", "mark an action handled"),
        ("/action dismiss <id>", "dismiss an action"),
    ],
    "Output": [
        ("/report [id]", "preview final report"),
        ("/diff [id]", "preview diff"),
        ("/dashboard", "print dashboard URL"),
    ],
    "System": [("/refresh", "refresh"), ("/doctor", "check setup"), ("/help", "show this menu"), ("/quit", "exit")],
}


def daemon_chat_view(
    *,
    workspace: Path,
    version: str,
    host: str,
    api_port: int,
    ui_port: int,
    daemon: dict[str, Any] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    task_payload: dict[str, Any] | None = None,
    approvals: list[dict[str, Any]] | None = None,
    provider_actions: list[dict[str, Any]] | None = None,
    command: str = "",
    message: str = "",
) -> Group:
    daemon = daemon or {}
    tasks = tasks or []
    approvals = approvals or []
    provider_actions = provider_actions or []
    summary = (
        f"muxdev v{version}\nlocal AI coding control plane\n"
        "task lifecycle | approvals | reports | diffs | skills\n\n"
        f"workspace  {workspace}\ndaemon     http://{host}:{api_port}\n"
        f"dashboard  http://{host}:{ui_port}\n"
        f"tasks={daemon.get('tasks', len(tasks))}  running={daemon.get('running_tasks', 0)}  "
        f"queue={daemon.get('queue_length', 0)}  approvals={len(approvals)}  provider_actions={len(provider_actions)}"
    )
    warnings = "\n".join(str(item) for item in daemon.get("warnings", []) if item)
    if warnings:
        summary += f"\n{warnings}"
    result: list[object] = [Panel(summary, title="muxdev", box=box.ASCII, border_style="cyan")]
    result.append(_daemon_focus_panel(task_payload=task_payload, tasks=tasks, approvals=approvals, provider_actions=provider_actions))
    if message:
        result.append(Panel(message, title=command or "Result", box=box.ASCII))
    result.append(Text("muxdev > describe a task, or use /dev <task>  /actions  /help  /quit", style="dim"))
    return Group(*result)


def daemon_help_text() -> str:
    lines: list[str] = []
    for group, commands in COMMAND_GROUPS.items():
        lines.append(f"{group}:")
        lines.extend(f"  {command:<24} {description}" for command, description in commands)
    return "\n".join(lines)


def daemon_tasks_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No daemon tasks yet.\nStart with /run <task>."
    lines = ["Recent tasks:"]
    for row in rows[:12]:
        task_id = str(row.get("task_id") or row.get("run_id") or "-")
        lines.append(
            _clip(
                f"  {task_id:<20} {str(row.get('status') or '-'):<18} "
                f"stage={str(row.get('current_stage') or '-'):<10} approvals={row.get('pending_approvals', 0):<2} "
                f"actions={row.get('pending_provider_actions', 0):<2} tokens={row.get('tokens', 0):<6} {row.get('task') or ''}",
                149,
            )
        )
        reason = _error_reason(row)
        if reason:
            lines.extend((f"    error: {_clip(reason, 100)}", f"    recover: /recover {task_id}  |  /report {task_id}"))
    return "\n".join(lines)


def daemon_approvals_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No pending approvals."
    return "Pending approvals:\n" + "\n".join(
        f"  {row.get('approval_id', '-')} task={row.get('run_id') or row.get('task_id') or '-'} "
        f"type={row.get('type', '-')} {row.get('reason', '')}"
        for row in rows[:12]
    )


def daemon_provider_actions_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No pending provider actions."
    lines = ["Pending provider actions:", "Handle the provider session, mark the action handled, then continue the task."]
    for row in rows[:12]:
        choices = row.get("choices") or row.get("options") or []
        labels = ", ".join(str(item.get("label") or item.get("value")) for item in choices if isinstance(item, dict))
        lines.extend(
            (
                f"  {row.get('action_id', '-')} task={row.get('run_id') or row.get('task_id') or '-'} "
                f"{row.get('input_kind') or row.get('kind') or '-'} {row.get('provider', '-')}/{row.get('stage_id', '-')}",
                f"    prompt: {_clip(row.get('prompt_text') or '', 120)}",
                f"    choices: {labels or '-'}  default={row.get('default_choice') or '-'}",
                f"    attach: {_clip(row.get('attach_command') or row.get('transcript_path') or '-', 120)}",
            )
        )
    return "\n".join(lines)


def daemon_task_detail_text(payload: dict[str, Any]) -> str:
    run = payload.get("run") or {}
    if not run:
        return "No selected task."
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    run_id = str(run.get("run_id") or payload.get("task_id") or "-")
    lines = [
        f"{run_id}: {run.get('status', '-')}",
        _clip(run.get("task") or "", 110),
        f"workflow={run.get('workflow', '-')} provider={run.get('provider', '-')} gate={context.get('gate') or '-'}",
        f"stage={_current_stage(payload)} approvals={summary.get('pending_approvals', 0)} "
        f"provider_actions={summary.get('pending_provider_actions', 0)} usage={summary.get('tokens', 0)} tokens",
    ]
    reason = _error_reason(payload)
    if reason:
        lines.extend(("", f"Error: {reason}", f"Recover: /recover {run_id}  |  /report {run_id}"))
    actions = [item for item in payload.get("provider_actions", []) if isinstance(item, dict) and item.get("status") == "pending"]
    if actions:
        lines.append("\nProvider actions:")
        for item in actions[:3]:
            lines.append(f"  {item.get('action_id')}: {item.get('prompt_text', '')}")
            lines.append(f"    attach: {item.get('attach_command') or item.get('transcript_path') or '-'}")
    events = [item for item in payload.get("trace", []) if isinstance(item, dict)][-5:]
    if events:
        lines.append("\nRecent events:")
        lines.extend(f"  {item.get('type', '-')} stage={item.get('stage') or '-'}" for item in events)
    return "\n".join(lines)


def daemon_report_text(payload: dict[str, Any], task_id: str) -> str:
    return _preview(str(payload.get("content") or ""), f"Full report: muxdev report {task_id}", 24)


def daemon_diff_text(payload: dict[str, Any], task_id: str) -> str:
    return _preview(str(payload.get("diff") or ""), f"Full diff: muxdev diff {task_id}", 24)


def _daemon_focus_panel(
    *,
    task_payload: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    provider_actions: list[dict[str, Any]],
) -> Panel:
    run = (task_payload or {}).get("run") or {}
    if not run:
        return Panel("No active task selected.\nStart with /dev <task> or /design <task>\nUse /help for commands", title="Current Task", box=box.ASCII)
    payload = task_payload or {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    grid = Table.grid(expand=True)
    grid.add_column(style="bold", no_wrap=True)
    grid.add_column(overflow="fold")
    grid.add_row("task", str(run.get("task") or ""))
    grid.add_row("id", str(run.get("run_id") or payload.get("task_id") or "-"))
    grid.add_row("status", str(run.get("status") or "-"))
    ux = payload.get("ux") if isinstance(payload.get("ux"), dict) else {}
    if ux:
        grid.add_row("next", str(ux.get("headline") or "-"))
        grid.add_row("why", str(ux.get("why") or ""))
    reason = _error_reason(payload)
    if reason:
        grid.add_row("error", reason)
        grid.add_row("recover", f"/recover {run.get('run_id') or 'latest'}  |  /report {run.get('run_id') or 'latest'}")
    grid.add_row("stage", _current_stage(payload))
    grid.add_row("usage", f"{summary.get('tokens', 0)} tokens  ${float(summary.get('cost_usd') or 0):.4f}")
    grid.add_row("approvals", str(summary.get("pending_approvals", len(approvals))))
    grid.add_row("provider actions", str(summary.get("pending_provider_actions", len(provider_actions))))
    return Panel(grid, title="Current Task", box=box.ASCII)


def _current_stage(payload: dict[str, Any]) -> str:
    for row in reversed(payload.get("stages", [])):
        if isinstance(row, dict) and row.get("status") not in {"completed", "skipped"}:
            return str(row.get("stage_id") or row.get("id") or "-")
    stages = payload.get("stages", [])
    return str(stages[-1].get("stage_id") or "-") if stages and isinstance(stages[-1], dict) else "-"


def _error_reason(payload: dict[str, Any]) -> str:
    item = payload.get("error_summary") if isinstance(payload.get("error_summary"), dict) else None
    if item is None:
        errors = [row for row in payload.get("errors", []) if isinstance(row, dict)] if isinstance(payload.get("errors"), list) else []
        item = errors[-1] if errors else None
    if not item:
        return ""
    stage = str(item.get("stage_id") or "run")
    kind = str(item.get("type") or item.get("error") or "")
    message = str(item.get("message") or "")
    return " ".join(part for part in (stage, kind + (":" if kind and message else ""), message) if part)


def _preview(content: str, footer: str, max_lines: int) -> str:
    lines = content.splitlines()
    preview = lines[:max_lines]
    if len(lines) > max_lines:
        preview.append(f"... {len(lines) - max_lines} more line(s)")
    preview.extend(("", footer))
    return "\n".join(preview)


def _clip(value: object, width: int) -> str:
    text = str(value)
    return text if len(text) <= width else text[: width - 3] + "..."
