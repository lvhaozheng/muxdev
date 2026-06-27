"""P4 conflict-aware parallel squad helpers."""

from __future__ import annotations

import json
from pathlib import Path

from ..storage import Blackboard


def detect_parallel_conflicts(stage_writes: dict[str, object]) -> list[dict[str, object]]:
    """Detect stages that plan to write the same normalized path."""
    normalized = _normalize_stage_writes(stage_writes)
    by_file: dict[str, list[str]] = {}
    for stage, paths in normalized.items():
        for path in paths:
            by_file.setdefault(path, []).append(stage)

    conflicts: list[dict[str, object]] = []
    for path, stages in sorted(by_file.items()):
        unique_stages = sorted(set(stages))
        if len(unique_stages) < 2:
            continue
        conflicts.append(
            {
                "stages": unique_stages,
                "files": [path],
                "severity": _conflict_severity(path),
                "status": "open",
                "reason": "multiple parallel stages plan to write the same file",
            }
        )
    return conflicts


def record_parallel_conflicts(
    blackboard: Blackboard,
    *,
    run_id: str | None,
    stage_id: str | None,
    stage_writes: dict[str, object],
) -> list[dict[str, object]]:
    """Persist detected parallel conflicts and return blackboard rows."""
    rows: list[dict[str, object]] = []
    for conflict in detect_parallel_conflicts(stage_writes):
        conflict_id = blackboard.add_parallel_conflict(
            run_id=run_id,
            stage_id=stage_id,
            stages=[str(item) for item in conflict["stages"]],
            files=[str(item) for item in conflict["files"]],
            severity=str(conflict["severity"]),
            status=str(conflict["status"]),
            resolution=str(conflict.get("reason") or ""),
        )
        rows.append({**conflict, "conflict_id": conflict_id, "run_id": run_id, "stage_id": stage_id})
    return rows


def write_parallel_conflict_report(run_dir: Path, *, run_id: str, conflicts: list[dict[str, object]]) -> Path:
    path = run_dir / "validation" / "parallel_conflicts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"contract_version": "muxdev.parallel_conflicts.v1", "run_id": run_id, "conflicts": conflicts}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def planned_stage_writes_from_automation(automation: dict[str, object], stages: list[str]) -> dict[str, list[str]]:
    """Read optional planned write hints from the automation payload."""
    raw = automation.get("parallel_writes") if isinstance(automation, dict) else None
    if not isinstance(raw, dict):
        return {}
    normalized = _normalize_stage_writes(raw)
    stage_set = set(stages)
    return {stage: paths for stage, paths in normalized.items() if stage in stage_set}


def _normalize_stage_writes(stage_writes: dict[str, object]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for stage, value in stage_writes.items():
        paths: list[object]
        if isinstance(value, dict):
            raw_paths = value.get("writes", value.get("files", []))
            paths = raw_paths if isinstance(raw_paths, list) else [raw_paths]
        elif isinstance(value, list):
            paths = value
        else:
            paths = [value]
        clean = sorted({_normalize_path(str(path)) for path in paths if str(path).strip()})
        if clean:
            normalized[str(stage)] = clean
    return normalized


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./").strip().lower()


def _conflict_severity(path: str) -> str:
    if path.endswith((".md", ".txt", ".rst")) or path.startswith("docs/"):
        return "medium"
    return "high"
