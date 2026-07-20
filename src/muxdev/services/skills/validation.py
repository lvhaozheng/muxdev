"""Skill validation, show, and doctor helpers."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .discovery import scan_skills, skill_from_file
from .parser import parse_skill_document


def skill_show(workspace: Path, name: str) -> dict[str, object]:
    return find_skill(workspace, name).to_dict(include_content=True)


def find_skill(workspace: Path, name: str):
    for skill in scan_skills(workspace, include_disabled=True):
        if skill.name == name:
            return skill
    path = (workspace / name).resolve() if not Path(name).is_absolute() else Path(name)
    if path.is_dir() and (path / "SKILL.md").exists():
        return skill_from_file(path / "SKILL.md", source="task", priority=900)
    if path.name == "SKILL.md" and path.exists():
        return skill_from_file(path, source="task", priority=900)
    raise ValueError(f"skill not found: {name}")


def validate_skill_path(path: Path, *, strict: bool = False) -> dict[str, object]:
    skill_file = path / "SKILL.md" if path.is_dir() else path
    errors: list[str] = []
    warnings: list[str] = []
    if not skill_file.exists():
        errors.append(f"SKILL.md not found: {skill_file}")
    else:
        try:
            info = skill_from_file(skill_file, source="validation", priority=0)
        except Exception as exc:
            return {"valid": False, "errors": [f"SKILL.md frontmatter invalid: {exc}"], "warnings": warnings, "path": str(skill_file)}
        errors.extend(info.validation_errors)
        warnings.extend(info.validation_warnings)
        if strict:
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", info.name):
                errors.append("skill name must be lowercase kebab-case")
            if len(info.description) > 1024:
                errors.append("description must be <= 1024 chars")
            if skill_file.parent.name != info.name:
                errors.append("parent dir must match skill name")
            meta, _ = parse_skill_document(skill_file)
            unexpected = sorted(set(meta) - {"name", "description"})
            if unexpected:
                errors.append("SKILL.md frontmatter supports only name and description: " + ", ".join(unexpected))
        if not info.description:
            warnings.append("description is required for automatic activation")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "migration_suggestions": _legacy_migration_suggestions(skill_file),
        "path": str(skill_file),
    }


def _legacy_migration_suggestions(skill_file: Path) -> list[dict[str, object]]:
    suggestions: list[dict[str, object]] = []
    policy_path = skill_file.parent / "muxdev.skill.toml"
    if policy_path.is_file():
        try:
            policy = tomllib.loads(policy_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            policy = {}
        delivery = policy.get("delivery_gate") if isinstance(policy, dict) else None
        if isinstance(delivery, dict):
            suggestions.append({
                "source": "muxdev.skill.toml:[delivery_gate]",
                "target": "evidence-policy.yaml:requirements",
                "legacy_keys": sorted(delivery),
                "automatic": False,
            })
    if skill_file.is_file() and "## Delivery Standard" in skill_file.read_text(encoding="utf-8", errors="replace"):
        suggestions.append({
            "source": "SKILL.md:Delivery Standard",
            "target": "evidence-policy.yaml:requirements",
            "automatic": False,
        })
    return suggestions
