"""Skill validation, show, and doctor helpers."""

from __future__ import annotations

import re
from pathlib import Path

from .discovery import load_skills_config, scan_all_skills, scan_skills, skill_from_file


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
        if not info.description:
            warnings.append("description is required for automatic activation")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "path": str(skill_file)}


def skill_doctor(workspace: Path) -> dict[str, object]:
    all_skills = scan_all_skills(workspace, include_disabled=True)
    names: dict[str, list[object]] = {}
    warnings: list[str] = []
    errors: list[str] = []
    for skill in all_skills:
        names.setdefault(skill.name, []).append(skill)
        if not Path(skill.skill_file).exists():
            errors.append(f"missing SKILL.md for {skill.name}: {skill.skill_file}")
        errors.extend(f"{skill.name}: {message}" for message in skill.validation_errors)
        warnings.extend(f"{skill.name}: {message}" for message in skill.validation_warnings)
        if skill.permissions.shell and skill.trust == "untrusted":
            errors.append(f"script requires shell but permission denied: {skill.name}")
    for name, rows in names.items():
        if len(rows) > 1:
            warnings.append(f"duplicate skill name uses highest priority: {name}")
    config = load_skills_config(workspace)
    available = {skill.name for skill in all_skills}
    for role, values in config.get("bind", {}).items():
        if isinstance(values, list):
            for value in values:
                if value not in available:
                    warnings.append(f"binding points to missing skill: {role}={value}")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "skills": [skill.to_dict() for skill in all_skills]}
