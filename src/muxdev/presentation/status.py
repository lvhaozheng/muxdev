"""Compact Rich rendering for daemon task details.

Rendering belongs to the presentation layer.  It deliberately accepts a plain
mapping so the CLI and tests do not need to know how task state is persisted.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table


def status_panel(payload: dict[str, Any]) -> Group:
    """Render the stable, daemon-backed task status surface."""
    panels = [_run_panel(payload)]
    if isinstance(payload.get("evidence_evaluation"), dict):
        panels.append(_evidence_panel(payload))
    panels.append(_activity_panel(payload))
    return Group(*panels)


def _run_panel(payload: dict[str, Any]) -> Panel:
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = Table.grid(padding=(0, 2))
    rows.add_column(style="bold")
    rows.add_column()
    rows.add_row("run", str(run.get("run_id") or payload.get("task_id") or "-"))
    rows.add_row("status", str(run.get("status") or "-"))
    rows.add_row("task", str(run.get("task") or "-"))
    rows.add_row("provider", str(run.get("provider") or "-"))
    rows.add_row("workflow", str(run.get("workflow") or "-"))
    rows.add_row("progress", _progress(summary))

    stages = Table(show_header=True, header_style="bold", expand=True)
    stages.add_column("Stage")
    stages.add_column("State")
    stages.add_column("Summary")
    for stage in payload.get("stages", []) if isinstance(payload.get("stages"), list) else []:
        if isinstance(stage, dict):
            stages.add_row(
                str(stage.get("stage_id") or "-"),
                str(stage.get("status") or "-"),
                str(stage.get("summary") or ""),
            )
    return Panel(Group(rows, stages), title="Task Status", border_style="cyan")


def _evidence_panel(payload: dict[str, Any]) -> Panel:
    evaluation = payload.get("evidence_evaluation") or {}
    manifest = payload.get("evidence_manifest") if isinstance(payload.get("evidence_manifest"), dict) else {}
    rows = Table.grid(padding=(0, 2))
    rows.add_column(style="bold")
    rows.add_column()
    rows.add_row("label", str(evaluation.get("label") or "-"))
    rows.add_row("confidence", str(evaluation.get("confidence", 0)))
    rows.add_row("events", str(manifest.get("event_count", 0)))
    reasons = evaluation.get("reasons") if isinstance(evaluation.get("reasons"), list) else []
    missing = evaluation.get("missing_evidence") if isinstance(evaluation.get("missing_evidence"), list) else []
    rows.add_row("why", "; ".join(str(item) for item in reasons[:3]) or "-")
    rows.add_row("missing", "; ".join(str(item) for item in missing[:3]) or "none")
    return Panel(rows, title="Evidence Evaluation", border_style="green")


def _activity_panel(payload: dict[str, Any]) -> Panel:
    rows = Table(show_header=True, header_style="bold", expand=True)
    rows.add_column("Event")
    rows.add_column("Stage")
    rows.add_column("Data")
    trace = payload.get("trace") if isinstance(payload.get("trace"), list) else []
    for event in trace[-6:]:
        if not isinstance(event, dict):
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        text = ", ".join(f"{key}={value}" for key, value in list(data.items())[:2])
        rows.add_row(str(event.get("type") or "-"), str(event.get("stage") or "-"), text)
    return Panel(rows, title="Recent Events", border_style="bright_black")


def _progress(summary: dict[str, Any]) -> str:
    done = int(summary.get("stage_done") or 0)
    total = int(summary.get("stage_total") or 0)
    percent = int(summary.get("progress") or 0)
    return f"{done}/{total} stages ({percent}%)"


__all__ = ["status_panel"]
