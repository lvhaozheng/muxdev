"""Trace readers used by CLI, TUI, reports, and tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_recent_trace(run_dir: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Read only the most recent trace events from trace.jsonl."""
    if limit <= 0:
        return []
    path = run_dir / "trace.jsonl"
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            data = b""
            offset = size
            block_size = 8192
            while offset > 0 and data.count(b"\n") <= limit:
                read_size = min(block_size, offset)
                offset -= read_size
                handle.seek(offset)
                data = handle.read(read_size) + data
                block_size = min(block_size * 2, 1024 * 1024)
    except OSError:
        return []

    lines = data.splitlines()
    if offset > 0 and lines:
        lines = lines[1:]
    events: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        if not raw.strip():
            continue
        events.append(json.loads(raw.decode("utf-8")))
    return events


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
