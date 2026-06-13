"""Small local skill evaluation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import read_skill_events
from .selector import select_skills
from .validation import find_skill


def eval_skill(workspace: Path, name: str, *, role: str | None = None, provider: str = "mock") -> dict[str, Any]:
    skill = find_skill(workspace, name)
    eval_dir = Path(skill.path) / "evals"
    positives = _read_jsonl(eval_dir / "activation.jsonl")
    negatives = _read_jsonl(eval_dir / "negative.jsonl")
    positive_passed = 0
    negative_passed = 0
    failures: list[dict[str, object]] = []
    for row in positives:
        task = str(row.get("task") or row.get("input") or "")
        selected = select_skills(workspace, task=task, roles=[role] if role else list(skill.roles), provider=provider).selected
        matched = any(item.skill.name == name for item in selected)
        positive_passed += int(matched)
        if not matched:
            failures.append({"kind": "activation", "task": task, "expected": "selected"})
    for row in negatives:
        task = str(row.get("task") or row.get("input") or "")
        selected = select_skills(workspace, task=task, roles=[role] if role else [], provider=provider).selected
        matched = any(item.skill.name == name for item in selected)
        negative_passed += int(not matched)
        if matched:
            failures.append({"kind": "negative", "task": task, "expected": "not_selected"})
    total = len(positives) + len(negatives)
    passed = positive_passed + negative_passed
    return {
        "skill": name,
        "provider": provider,
        "role": role,
        "passed": passed,
        "total": total,
        "success": passed == total,
        "failures": failures,
    }


def score_skill(workspace: Path, name: str, *, last: str = "30d") -> dict[str, Any]:
    events = [event for event in read_skill_events(workspace) if event.get("skill") == name and event.get("kind") == "skill_activation"]
    return {
        "skill": name,
        "last": last,
        "activations": len(events),
        "providers": sorted({str(event.get("provider")) for event in events if event.get("provider")}),
        "roles": sorted({str(event.get("role")) for event in events if event.get("role")}),
        "recommendation": "keep" if events else "needs_data",
    }


def abtest_skill(workspace: Path, name: str, *, versions: list[str], provider: str = "mock") -> dict[str, Any]:
    results = [{"version": version, **eval_skill(workspace, name, provider=provider)} for version in versions]
    best = max(results, key=lambda row: (int(row.get("passed", 0)), str(row.get("version", "")))) if results else None
    return {"skill": name, "provider": provider, "versions": versions, "results": results, "recommendation": best}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows
