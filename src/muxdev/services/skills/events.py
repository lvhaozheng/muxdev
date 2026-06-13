"""Skill event persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...models import utc_now


def append_skill_event(workspace: Path, event: dict[str, Any]) -> Path:
    payload = {"created_at": utc_now(), **event}
    path = workspace / ".muxdev" / "skill-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def read_skill_events(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / ".muxdev" / "skill-events.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows
