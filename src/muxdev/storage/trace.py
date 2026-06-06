"""Trace readers used by CLI, TUI, reports, and tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_trace(run_dir: Path) -> list[dict[str, Any]]:
    """Read trace.jsonl from a run directory, returning an empty list if absent."""
    path = run_dir / "trace.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def compact_trace(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project verbose trace events into dashboard-friendly rows."""
    rows: list[dict[str, Any]] = []
    for event in events:
        rows.append(
            {
                "time": event.get("time", ""),
                "type": event.get("type", ""),
                "stage": event.get("stage") or "",
                "data": event.get("data", {}),
            }
        )
    return rows
