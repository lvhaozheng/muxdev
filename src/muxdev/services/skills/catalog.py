"""Progressive-disclosure skill catalog builder."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .discovery import scan_skills
from .model import SkillInfo, public_skill_payload


@dataclass(frozen=True)
class SkillCatalogItem:
    skill: SkillInfo

    def to_dict(self) -> dict[str, object]:
        return public_skill_payload(self.skill)


@dataclass(frozen=True)
class SkillCatalog:
    items: list[SkillCatalogItem]
    omitted: list[dict[str, object]] = field(default_factory=list)
    budget: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "skills": [item.to_dict() for item in self.items],
            "omitted": self.omitted,
            "budget": self.budget,
        }


def build_skill_catalog(
    workspace: Path,
    *,
    role: str | None = None,
    stage: str | None = None,
    budget: int | None = None,
) -> SkillCatalog:
    """Build Level 1 metadata only; never includes SKILL.md content."""
    rows = []
    for skill in scan_skills(workspace):
        if role and skill.roles and role not in skill.roles:
            continue
        if stage and skill.stages and stage not in skill.stages:
            continue
        rows.append(skill)
    used = 0
    items: list[SkillCatalogItem] = []
    omitted: list[dict[str, object]] = []
    for skill in rows:
        payload = public_skill_payload(skill)
        size = len(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        if budget is not None and used + size > budget:
            omitted.append({"name": skill.name, "reason": "catalog_budget", "estimated_chars": size})
            continue
        used += size
        items.append(SkillCatalogItem(skill))
    return SkillCatalog(
        items=items,
        omitted=omitted,
        budget={
            "max_chars": budget,
            "used_chars": used,
            "omitted": len(omitted),
        },
    )
