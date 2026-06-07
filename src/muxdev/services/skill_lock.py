"""Skill lock and skill-memory helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..storage.contracts import sha256_file
from ..storage.memory import MemoryStore
from .skill_engine import scan_skills


def write_skill_lock(workspace: Path, *, promote_memory: bool = True) -> dict[str, Any]:
    """Write `.muxdev/skill-lock.json` from discovered skills."""
    skills = scan_skills(workspace, include_disabled=True)
    rows: list[dict[str, Any]] = []
    for skill in skills:
        skill_file = Path(skill.skill_file)
        version = _skill_version(skill_file)
        roles = _compatible_roles(skill_file)
        rows.append(
            {
                "name": skill.name,
                "version": version,
                "skill_hash": sha256_file(skill_file) if skill_file.exists() else "",
                "path": skill.path,
                "skill_file": skill.skill_file,
                "compatible_roles": roles,
                "source": skill.source,
                "trust": skill.trust,
                "disabled": skill.disabled,
            }
        )
    payload = {"contract_version": "muxdev.skill_lock.v1", "skills": sorted(rows, key=lambda row: str(row["name"]))}
    path = workspace / ".muxdev" / "skill-lock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    memories: list[dict[str, object]] = []
    if promote_memory:
        with MemoryStore(workspace) as store:
            for row in payload["skills"]:
                memories.append(
                    store.propose_claim(
                        claim=f"Skill {row['name']} is locked at {row['skill_hash']} for roles: {', '.join(row['compatible_roles']) or 'any'}",
                        scope="project",
                        kind="skill_memory",
                        role="any",
                        confidence=0.65,
                        evidence=[{"kind": "skill_lock", "path": str(path), "summary": f"skill lock for {row['name']}"}],
                    )
                )
    return {"path": str(path), "skills": payload["skills"], "memory_proposals": memories}


def _skill_version(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    for line in text.splitlines()[:40]:
        stripped = line.strip()
        if stripped.startswith("version:"):
            return stripped.split(":", 1)[1].strip().strip('"')
    return None


def _compatible_roles(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    for line in text.splitlines()[:60]:
        stripped = line.strip()
        if stripped.startswith("compatible_roles:"):
            raw = stripped.split(":", 1)[1].strip().strip("[]")
            return [item.strip().strip("'\"") for item in raw.split(",") if item.strip()]
        if stripped.startswith("role:"):
            return [stripped.split(":", 1)[1].strip().strip("'\"")]
    return []
