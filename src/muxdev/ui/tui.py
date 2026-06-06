"""Rich/prompt_toolkit terminal UI for muxdev.

The TUI is intentionally lightweight: it renders the same blackboard/trace state
that CLI commands expose, then offers slash-command shortcuts for common human
supervision tasks such as running mock workflows, reviewing approvals, checking
providers, and opening reports.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from rich import box
from rich.align import Align
from rich.console import Group
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import __version__
from ..config.loader import load_config, path_config
from ..models import ApprovalStatus
from ..providers import detect_providers
from ..providers import probe_provider
from ..services.reports import generate_final_report
from ..services.dashboard import build_run_dashboard_payload, startup_dashboard_payload
from ..runtime import SupervisorRuntime
from ..core.safety import SafetyPolicyEngine
from ..services.skills import SkillRegistry
from ..storage import Blackboard, RunStore, compact_trace, read_trace


SLASH_COMMANDS = load_config().get("ui", {}).get("slash_commands", {})

MUXDEV_ASCII_LOGO = r"""
 __  __ _   ___  _____  _____   __
|  \/  | | | \ \/ / _ \ / _ \ \ / /
| |\/| | |_| |>  <  __/  __/\ V /
|_|  |_|\__,_/_/\_\___|\___| \_/
""".strip("\n")

DAEMON_COMMAND_GROUPS: dict[str, list[tuple[str, str]]] = {
    "Work": [
        ("/run <task>", "submit a daemon task"),
        ("/continue [id]", "continue a paused task"),
        ("/stop <id>", "abort a task"),
        ("/status [id]", "show compact task detail"),
        ("/tasks", "list recent tasks"),
    ],
    "Review": [
        ("/approvals", "list pending approvals"),
        ("/approve <id>", "approve an item"),
        ("/deny <id>", "deny an item"),
    ],
    "Output": [
        ("/report [id]", "preview final report"),
        ("/diff [id]", "preview diff"),
        ("/dashboard", "print local dashboard URL"),
    ],
    "System": [
        ("/refresh", "refresh daemon summary"),
        ("/start", "start daemon"),
        ("/help", "show this menu"),
        ("/quit", "exit TUI"),
    ],
}


def status_payload(run_dir: Path, run_id: str) -> dict[str, Any]:
    """Collect all data needed to render a run status dashboard."""
    blackboard = Blackboard(run_dir)
    try:
        return build_run_dashboard_payload(run_dir.parents[2] if len(run_dir.parents) > 2 else Path.cwd(), run_dir, run_id, blackboard)
    finally:
        blackboard.close()


def startup_payload(workspace: Path) -> dict[str, Any]:
    """Return a dashboard payload for workspaces with no completed runs yet."""
    return startup_dashboard_payload(workspace)


def load_payload(workspace: Path, run_id: str = "latest") -> dict[str, Any]:
    """Resolve a run id and return either run status or startup status."""
    store = RunStore(workspace)
    resolved = store.latest_run_id() if run_id == "latest" else run_id
    return status_payload(store.find_run_dir(resolved), resolved) if resolved else startup_payload(workspace)


def start_tui(workspace: Path, run_id: str = "latest") -> None:
    """Start the interactive TUI, or render once when stdin is non-interactive."""
    console = Console(width=120)
    if not sys.stdin.isatty():
        console.print(status_panel(load_payload(workspace, run_id)))
        return

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
    except Exception:
        console.print(status_panel(load_payload(workspace, run_id)))
        console.print("prompt_toolkit is unavailable; rendered dashboard once.")
        return

    completer = WordCompleter(
        list(_slash_commands(workspace)),
        meta_dict=_slash_commands(workspace),
        ignore_case=True,
        sentence=True,
    )
    session = PromptSession(completer=completer, complete_while_typing=True)
    current_run = run_id
    _render_tui(console, workspace, current_run, clear_screen=True)
    while True:
        line = session.prompt("muxdev> ").strip()
        normalized = _normalize_command(line)
        if not normalized:
            continue
        if normalized in {"q", "quit", "exit"}:
            break
        message = _handle_tui_command(normalized, workspace, current_run)
        if normalized.startswith("run "):
            latest = RunStore(workspace).latest_run_id()
            if latest:
                current_run = latest
        _render_tui(console, workspace, current_run, message)


def _render_tui(console: Console, workspace: Path, run_id: str, message: str = "", *, clear_screen: bool = False) -> None:
    if clear_screen:
        console.clear()
    console.print(status_panel(load_payload(workspace, run_id)))
    if message:
        console.print(Panel(message, title="Message", box=box.ASCII, border_style="cyan"))
    console.print("[dim]Type / for commands. Common: /run <task> | /resume | /report | /trace | /approve <id> | /quit[/dim]")


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
    command: str = "",
    message: str = "",
) -> Group:
    """Build the daemon-backed conversational TUI frame.

    This renderer is intentionally data-only: callers fetch daemon state and
    pass it in, so drawing the TUI never probes provider CLIs or mutates state.
    """
    return Group(
        _daemon_hero_panel(workspace=workspace, version=version, host=host, api_port=api_port, ui_port=ui_port, daemon=daemon, tasks=tasks or [], approvals=approvals or []),
        _daemon_focus_panel(task_payload=task_payload, tasks=tasks or [], approvals=approvals or []),
        _daemon_result_panel(command, message) if message else Text(""),
        Text("muxdev > /run <task>   /tasks   /status [id]   /approvals   /help   /quit", style="dim"),
    )


def daemon_help_text() -> str:
    lines: list[str] = []
    for group, commands in DAEMON_COMMAND_GROUPS.items():
        lines.append(f"{group}:")
        lines.extend(f"  {command:<18} {description}" for command, description in commands)
    return "\n".join(lines)


def daemon_tasks_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No daemon tasks yet.\nStart with /run <task>."
    lines = ["Recent tasks:"]
    for row in rows[:12]:
        lines.append(
            "  {task_id:<22} {status:<17} stage={stage:<12} approvals={approvals:<2} tokens={tokens:<6} {task}".format(
                task_id=_clip(row.get("task_id") or row.get("run_id") or "-", 22),
                status=_clip(row.get("status") or "-", 17),
                stage=_clip(row.get("current_stage") or "-", 12),
                approvals=row.get("pending_approvals", 0),
                tokens=row.get("tokens", 0),
                task=_clip(row.get("task") or "", 58),
            )
        )
    if len(rows) > 12:
        lines.append(f"  ... {len(rows) - 12} more")
    return "\n".join(lines)


def daemon_approvals_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No pending approvals."
    lines = ["Pending approvals:"]
    for row in rows[:12]:
        lines.append(
            "  {approval:<18} task={task:<22} type={kind:<10} {reason}".format(
                approval=_clip(row.get("approval_id") or "-", 18),
                task=_clip(row.get("run_id") or row.get("task_id") or "-", 22),
                kind=_clip(row.get("type") or "-", 10),
                reason=_clip(row.get("reason") or "", 70),
            )
        )
    if len(rows) > 12:
        lines.append(f"  ... {len(rows) - 12} more")
    return "\n".join(lines)


def daemon_task_detail_text(payload: dict[str, Any]) -> str:
    run = payload.get("run") or {}
    if not run:
        return "No selected task."
    context = payload.get("context", {}) if isinstance(payload.get("context"), dict) else {}
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    skills = _skill_names(context.get("skills", []))
    lines = [
        f"{run.get('run_id') or payload.get('task_id')}: {run.get('status', '-')}",
        _clip(run.get("task") or "", 110),
        f"workflow={run.get('workflow', '-')} provider={run.get('provider', '-')} profile={context.get('profile') or '-'} gate={context.get('gate') or '-'}",
        f"stage={_current_stage(payload)} approvals={summary.get('pending_approvals', 0)} usage={summary.get('tokens', 0)} tokens ${float(summary.get('cost_usd') or 0):.4f}",
        f"skills={', '.join(skills) if skills else '-'}",
    ]
    events = _recent_event_lines(payload.get("trace", []), limit=5)
    if events:
        lines.append("")
        lines.append("Recent events:")
        lines.extend(f"  {line}" for line in events)
    return "\n".join(lines)


def daemon_report_text(payload: dict[str, Any], task_id: str) -> str:
    content = str(payload.get("content") or "")
    return _preview_block(content, empty="(empty report)", footer=f"Full report: muxdev report {task_id}")


def daemon_diff_text(payload: dict[str, Any], task_id: str) -> str:
    content = str(payload.get("diff") or "")
    return _preview_block(content, empty="(empty diff)", footer=f"Full diff: muxdev diff {task_id}", max_lines=24)


def _daemon_hero_panel(
    *,
    workspace: Path,
    version: str,
    host: str,
    api_port: int,
    ui_port: int,
    daemon: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
) -> Panel:
    daemon = daemon or {}
    running = daemon.get("running_tasks", 0)
    queue = daemon.get("queue_length", 0)
    task_count = daemon.get("tasks", len(tasks))
    body = Table.grid(expand=True)
    body.add_column(ratio=2)
    body.add_column(ratio=3)
    body.add_row(
        Text(MUXDEV_ASCII_LOGO, style="bold cyan"),
        Group(
            Text(f"muxdev v{version}", style="bold cyan"),
            Text("local AI coding control plane", style="bold"),
            Text("task lifecycle | approvals | reports | diffs | skills", style="dim"),
            Text(""),
            Text(f"workspace  {_display_path(workspace, 78)}", style="dim"),
            Text(f"daemon     http://{host}:{api_port}", style="dim"),
            Text(f"dashboard  http://{host}:{ui_port}", style="dim"),
            Text(f"tasks={task_count}  running={running}  queue={queue}  approvals={len(approvals)}", style="cyan"),
        ),
    )
    return Panel(body, title="muxdev", box=box.ASCII, border_style="cyan", padding=(1, 2))


def _daemon_focus_panel(
    *,
    task_payload: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
) -> Panel:
    if not task_payload or not (task_payload.get("run") or {}):
        lines = [
            Text("No active task selected.", style="bold"),
            Text("Start with /run <task>", style="cyan"),
            Text("Open dashboard with /dashboard", style="cyan"),
            Text("Use /help for commands", style="cyan"),
        ]
        if tasks:
            lines.append(Text(""))
            lines.append(Text(f"{len(tasks)} task(s) are available. Use /tasks or /status <id>.", style="dim"))
        return Panel(Group(*lines), title="Current Task", box=box.ASCII, border_style="bright_black", padding=(1, 2))

    run = task_payload.get("run") or {}
    context = task_payload.get("context", {}) if isinstance(task_payload.get("context"), dict) else {}
    summary = task_payload.get("summary", {}) if isinstance(task_payload.get("summary"), dict) else {}
    skills = _skill_names(context.get("skills", []))
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1, style="bold")
    grid.add_column(ratio=4)
    grid.add_row("task", _clip(run.get("task") or "", 104))
    grid.add_row("id", str(run.get("run_id") or task_payload.get("task_id") or "-"))
    grid.add_row("status", _status_text(str(run.get("status") or "-")))
    grid.add_row("stage", _current_stage(task_payload))
    grid.add_row("profile/gate", f"{context.get('profile') or '-'} / {context.get('gate') or '-'}")
    grid.add_row("usage", f"{summary.get('tokens', 0)} tokens  ${float(summary.get('cost_usd') or 0):.4f}")
    grid.add_row("approvals", str(summary.get("pending_approvals", len(approvals))))
    grid.add_row("skills", ", ".join(skills) if skills else "-")
    events = _recent_event_lines(task_payload.get("trace", []), limit=5)
    if events:
        grid.add_row("recent", "\n".join(events))
    return Panel(grid, title="Current Task", box=box.ASCII, border_style=_status_border(str(run.get("status") or "")), padding=(1, 2))


def _daemon_result_panel(command: str, message: str) -> Panel:
    title = f"Result {command}".strip()
    return Panel(_clip(message, 4000), title=title, box=box.ASCII, border_style="cyan", padding=(1, 2))


def _current_stage(payload: dict[str, Any]) -> str:
    for row in payload.get("stages", []) or []:
        if row.get("status") == "running":
            return str(row.get("stage_id") or "-")
    run = payload.get("run") or {}
    return str(run.get("current_stage") or "-")


def _recent_event_lines(rows: object, *, limit: int) -> list[str]:
    if not isinstance(rows, list):
        return []
    result: list[str] = []
    for row in rows[-limit:]:
        if not isinstance(row, dict):
            continue
        data = row.get("data", {})
        if isinstance(data, dict):
            summary = ", ".join(f"{key}={_display_value(value)}" for key, value in list(data.items())[:2])
        else:
            summary = str(data)
        result.append(_clip(f"{row.get('type', 'event')} {row.get('stage') or '-'} {summary}".strip(), 106))
    return result


def _skill_names(rows: object) -> list[str]:
    if not isinstance(rows, list):
        return []
    result: list[str] = []
    for item in rows:
        if isinstance(item, dict) and item.get("name"):
            result.append(str(item["name"]))
        elif item:
            result.append(str(item))
    return result


def _preview_block(content: str, *, empty: str, footer: str, max_lines: int = 18, max_chars: int = 2400) -> str:
    if not content:
        return empty
    lines = content.splitlines()
    preview = "\n".join(lines[:max_lines])
    if len(preview) > max_chars:
        preview = preview[:max_chars].rstrip() + "\n..."
    elif len(lines) > max_lines:
        preview += "\n..."
    return f"{preview}\n\n{footer}"


def _status_border(status: str) -> str:
    if status in {"completed"}:
        return "green"
    if status in {"running", "created"}:
        return "cyan"
    if status in {"awaiting_approval", "paused_budget"}:
        return "yellow"
    if status in {"blocked", "aborted", "failed"}:
        return "red"
    return "bright_black"


def _normalize_command(line: str) -> str:
    """Translate slash commands into the compact internal command grammar."""
    text = line.strip()
    if not text.startswith("/"):
        return text
    if text == "/refresh":
        return "r"
    if text == "/quit":
        return "q"
    if text.startswith("/run "):
        return "run " + text.split(maxsplit=1)[1]
    if text.startswith("/approve "):
        return "approve " + text.split(maxsplit=1)[1]
    if text.startswith("/deny "):
        return "deny " + text.split(maxsplit=1)[1]
    if text.startswith("/provider doctor "):
        return "provider doctor " + text.split(maxsplit=2)[2]
    if text.startswith("/skill install "):
        return "skill install " + text.split(maxsplit=2)[2]
    if text.startswith("/new "):
        return "new " + text.split(maxsplit=1)[1]
    mapping = {
        "/help": "help",
        "/resume": "resume",
        "/report": "report",
        "/trace": "trace",
        "/providers": "providers",
        "/agents": "agents",
        "/skills": "skills",
        "/usage": "usage",
        "/approvals": "approvals",
        "/repl": "repl",
    }
    return mapping.get(text, text)


def _handle_tui_command(line: str, workspace: Path, run_id: str) -> str:
    """Execute one normalized TUI command and return a user-facing message."""
    if not line or line == "r":
        return "refreshed"
    if line == "help":
        return "\n".join(f"{command:<12} {description}" for command, description in _slash_commands(workspace).items())
    if line == "providers":
        probes = detect_providers()
        return "\n".join(f"{probe.provider}: {probe.status}" for probe in probes)
    if line.startswith("provider doctor "):
        probe = probe_provider(line.split(maxsplit=2)[2])
        return "\n".join(f"{key}: {value}" for key, value in probe.to_dict().items())
    if line == "agents":
        rows = _latest_rows(workspace, "agents")
        if not rows:
            return "no agents recorded"
        return "\n".join(f"{row['role']}: {row['provider']} ({row['status']})" for row in rows)
    if line == "skills":
        skills = SkillRegistry(workspace).list()
        if not skills:
            return "no skills installed"
        return "\n".join(f"{skill.name}: {skill.path}" for skill in skills)
    if line.startswith("skill install "):
        name = line.split(maxsplit=2)[2]
        record = SkillRegistry(workspace).install(name, native=True, provider="generic")
        return f"installed skill {record.name}: {record.path}"
    if line == "usage":
        rows = _latest_rows(workspace, "usage_records")
        if not rows:
            return "no usage recorded"
        total = sum(float(row["cost_usd"]) for row in rows)
        tokens = sum(int(row["tokens"]) for row in rows)
        return f"tokens={tokens} cost_usd={total:.4f}"
    if line == "approvals":
        pending = _pending_approvals(workspace)
        if not pending:
            return "no pending approvals"
        return "\n".join(f"{row['approval_id']} {row['type']} {row['reason']}" for row in pending)
    if line == "repl":
        return "Open a separate shell and run: muxdev repl"
    if line.startswith("run "):
        result = SupervisorRuntime(workspace).run(line.split(maxsplit=1)[1], provider="mock")
        return f"started {result.run_id}: {result.status}"
    if line.startswith("new "):
        target = (workspace / line.split(maxsplit=1)[1]).resolve()
        (path_config(target, "runtime_root") / "workflows").mkdir(parents=True, exist_ok=True)
        return f"created {target}"
    if line.startswith("!"):
        result = SafetyPolicyEngine().evaluate_shell(line[1:].strip())
        return f"{result.decision}: {result.reason}"
    if line == "resume":
        latest = RunStore(workspace).latest_run_id()
        if not latest:
            return "no run to resume"
        result = SupervisorRuntime(workspace).resume(latest)
        return f"resumed {result.run_id}: {result.status}"
    if line == "report":
        latest = RunStore(workspace).latest_run_id() if run_id == "latest" else run_id
        if not latest:
            return "no report yet"
        run_dir = RunStore(workspace).find_run_dir(latest)
        blackboard = Blackboard(run_dir)
        try:
            path = run_dir / "final_report.md"
            if not path.exists():
                path = generate_final_report(run_dir, latest, blackboard)
            return path.read_text(encoding="utf-8")
        finally:
            blackboard.close()
    if line == "trace":
        latest = RunStore(workspace).latest_run_id() if run_id == "latest" else run_id
        if not latest:
            return "no trace yet"
        events = compact_trace(read_trace(RunStore(workspace).find_run_dir(latest)))[-10:]
        return "\n".join(f"{event['type']} {event['stage']}".strip() for event in events)
    if line.startswith("approve "):
        return _decide_approval(workspace, line.split(maxsplit=1)[1], ApprovalStatus.APPROVED)
    if line.startswith("deny "):
        return _decide_approval(workspace, line.split(maxsplit=1)[1], ApprovalStatus.DENIED)
    return "unknown command"


def _latest_rows(workspace: Path, table: str) -> list[dict[str, object]]:
    latest = RunStore(workspace).latest_run_id()
    if not latest:
        return []
    blackboard = Blackboard(RunStore(workspace).find_run_dir(latest))
    try:
        return blackboard.table_rows(table)
    finally:
        blackboard.close()


def _pending_approvals(workspace: Path) -> list[dict[str, object]]:
    store = RunStore(workspace)
    if not store.runs_dir.exists():
        return []
    rows: list[dict[str, object]] = []
    for run_dir in store.runs_dir.iterdir():
        if not (run_dir / "blackboard.sqlite").exists():
            continue
        blackboard = Blackboard(run_dir)
        try:
            rows.extend(blackboard.list_approvals(status=str(ApprovalStatus.PENDING), run_id=run_dir.name))
        finally:
            blackboard.close()
    return rows


def _decide_approval(workspace: Path, approval_id: str, status: ApprovalStatus) -> str:
    store = RunStore(workspace)
    if not store.runs_dir.exists():
        return f"approval not found: {approval_id}"
    for run_dir in store.runs_dir.iterdir():
        if not (run_dir / "blackboard.sqlite").exists():
            continue
        blackboard = Blackboard(run_dir)
        try:
            rows = blackboard.list_approvals(run_id=run_dir.name)
            if any(row["approval_id"] == approval_id for row in rows):
                blackboard.decide_approval(approval_id, status)
                return f"{approval_id}: {status}"
        finally:
            blackboard.close()
    return f"approval not found: {approval_id}"


def app_payload(workspace: Path) -> dict[str, Any]:
    probes = detect_providers()
    ready = [probe.provider for probe in probes if probe.status == "ready"]
    partial = [probe.provider for probe in probes if probe.status == "partial"]
    return {
        "name": "muxdev",
        "version": __version__,
        "workspace": str(workspace),
        "providers": {
            "ready": ready,
            "partial": partial,
            "total": len(probes),
        },
    }


def status_panel(payload: dict[str, Any]) -> Group:
    return Group(
        _overview_panel(payload),
        _work_panel(payload),
        _trace_panel(payload),
    )


def _overview_panel(payload: dict[str, Any]) -> Panel:
    app = payload["app"]
    body = Table.grid(expand=True)
    body.add_column(ratio=3)
    body.add_column(ratio=2)
    body.add_column(ratio=3)
    body.add_row(
        _overview_section("Session", _session_summary(payload)),
        _overview_section("Providers", _provider_summary(payload)),
        _overview_section("Quick Commands", _quick_summary(payload)),
    )
    title = Text.assemble(
        ("muxdev", "bold cyan"),
        (f"  v{app['version']}", "dim"),
        ("  local agent control plane", "dim"),
    )
    return Panel(
        Group(title, Text(f"workspace  {_display_path(app['workspace'], 104)}", style="dim"), Text(""), body),
        box=box.ASCII,
        border_style="cyan",
        padding=(1, 2),
    )


def _overview_section(title: str, table: Table) -> Group:
    return Group(Text(title, style="bold cyan"), table)


def _session_summary(payload: dict[str, Any]) -> Table:
    run = payload.get("run")
    table = Table.grid(expand=True)
    table.add_column(ratio=1, style="bold")
    table.add_column(ratio=2)
    if not run:
        table.add_row("state", "ready")
        table.add_row("latest run", "none")
        table.add_row("next", "/run <task>")
        return table
    table.add_row("run", run["run_id"])
    table.add_row("status", _status_text(run["status"]))
    table.add_row("provider", run["provider"])
    table.add_row("workflow", run["workflow"])
    context = payload.get("context", {}) if isinstance(payload.get("context"), dict) else {}
    if context.get("profile"):
        table.add_row("profile", str(context.get("profile")))
    if context.get("gate"):
        table.add_row("gate", str(context.get("gate")))
    table.add_row("progress", _progress_text(payload))
    return table


def _provider_summary(payload: dict[str, Any]) -> Table:
    providers = payload["app"]["providers"]
    table = Table.grid(expand=True)
    table.add_column(ratio=1, style="bold")
    table.add_column(ratio=2)
    table.add_row("ready", ", ".join(providers["ready"]) or "-")
    table.add_row("partial", ", ".join(providers["partial"]) or "-")
    table.add_row("known", str(providers["total"]))
    table.add_row("doctor", "/providers")
    return table


def _quick_summary(payload: dict[str, Any]) -> Table:
    pending = [row for row in payload["approvals"] if row["status"] == "pending"]
    table = Table.grid(expand=True)
    table.add_column(ratio=1, style="bold")
    table.add_column(ratio=3)
    if pending:
        first = pending[0]
        table.add_row("approve", f"/approve {first['approval_id']}")
        table.add_row("deny", f"/deny {first['approval_id']}")
    else:
        table.add_row("approvals", "none pending")
    quick = _quick_commands()
    table.add_row("run", "/run <task>")
    table.add_row("continue", "/continue")
    table.add_row("report", quick.get("report", "muxdev report latest"))
    return table


def _work_panel(payload: dict[str, Any]) -> Panel:
    run = payload.get("run")
    if not run:
        lines = [
            Text("No active run yet.", style="bold"),
            Text(_quick_commands().get("run", 'muxdev dev "task" --provider mock'), style="cyan"),
            Text("Use /providers to check installed CLIs or /skills to inspect local skills.", style="dim"),
        ]
        return Panel(Group(*lines), title="Work Board", box=box.ASCII, border_style="bright_black", padding=(1, 2))

    meta = Table.grid(expand=True)
    meta.add_column(ratio=1, style="bold")
    meta.add_column(ratio=4)
    pending = [row for row in payload["approvals"] if row["status"] == "pending"]
    context = payload.get("context", {}) if isinstance(payload.get("context"), dict) else {}
    skills = context.get("skills", []) if isinstance(context.get("skills"), list) else []
    meta.add_row("worktree", _display_path(run["worktree"], 96))
    meta.add_row("pending approvals", str(len(pending)))
    meta.add_row("usage", f"{payload['summary']['tokens']} tokens  ${payload['summary']['cost_usd']:.4f}")
    if skills:
        meta.add_row("skills", ", ".join(str(skill.get("name", skill)) if isinstance(skill, dict) else str(skill) for skill in skills))

    stage_table = Table(box=None, show_header=True, header_style="bold dim", expand=True)
    stage_table.add_column("Stage", ratio=2)
    stage_table.add_column("State", ratio=1)
    stage_table.add_column("Summary", ratio=4)
    for row in payload["stages"]:
        stage_table.add_row(row["stage_id"], str(_status_text(row["status"])), _clip(row["summary"] or "", 64))
    return Panel(Group(meta, Text(""), stage_table), title="Work Board", box=box.ASCII, border_style="bright_black")


def _trace_panel(payload: dict[str, Any]) -> Panel:
    events = payload["trace"][-6:]
    if not events:
        return Panel(Align.left("No trace yet."), title="Recent Events", box=box.ASCII, border_style="bright_black")
    table = Table(box=None, show_header=True, header_style="bold dim", expand=True)
    table.add_column("Event")
    table.add_column("Stage")
    table.add_column("Data")
    for event in events:
        data = event.get("data", {})
        summary = _clip(", ".join(f"{key}={_display_value(value)}" for key, value in list(data.items())[:2]), 64)
        table.add_row(str(event["type"]), str(event["stage"] or "-"), summary)
    return Panel(table, title="Recent Events", box=box.ASCII, border_style="bright_black")


def _status_text(status: str) -> Text:
    styles = {
        "completed": "bold green",
        "running": "bold cyan",
        "awaiting_approval": "bold yellow",
        "paused_budget": "bold yellow",
        "blocked": "bold red",
        "failed": "bold red",
        "skipped": "dim",
    }
    return Text(status, style=styles.get(status, "white"))


def _progress_text(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    total = int(summary.get("stage_total") or 0)
    done = int(summary.get("stage_done") or 0)
    progress = int(summary.get("progress") or 0)
    return f"{done}/{total} stages  {progress}%"


def _clip(value: object, width: int) -> str:
    text = str(value)
    if len(text) <= width:
        return text
    return text[: max(0, width - 3)] + "..."


def _display_path(value: object, width: int) -> str:
    return _clip(str(value).replace("\\", "/"), width)


def _display_value(value: object) -> str:
    return str(value).replace("\\", "/")


def _slash_commands(workspace: Path | None = None) -> dict[str, str]:
    config = load_config(workspace or Path.cwd())
    commands = config.get("ui", {}).get("slash_commands", {})
    return {str(key): str(value) for key, value in commands.items()} if isinstance(commands, dict) else {}


def _quick_commands() -> dict[str, str]:
    commands = load_config().get("ui", {}).get("quick_commands", {})
    return {str(key): str(value) for key, value in commands.items()} if isinstance(commands, dict) else {}
