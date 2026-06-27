"""Explainable skill selection."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from ...config.runtime import normalize_role
from .adapters import injection_mode
from .catalog import build_skill_catalog
from .discovery import load_skills_config, role_bindings, skill_from_file
from .model import ActivatedSkill, SkillInfo, SkillSelection
from .trust import can_auto_activate, can_manual_activate


def select_skills(
    workspace: Path,
    *,
    task: str,
    roles: list[str] | None = None,
    stage: str | None = None,
    changed_files: list[str] | None = None,
    explicit: list[str] | None = None,
    provider: str = "mock",
    env: dict[str, str] | None = None,
    budget: int | None = None,
    auto: bool = True,
) -> SkillSelection:
    config = load_skills_config(workspace, env=env)
    catalog = build_skill_catalog(workspace, budget=budget)
    available = {item.skill.name: item.skill for item in catalog.items}
    selected: list[ActivatedSkill] = []
    rejected: list[dict[str, object]] = []
    warnings: list[str] = []
    selected_keys: set[tuple[str, str | None, str | None]] = set()

    for spec in explicit or []:
        role, name = _parse_skill_spec(spec)
        skill = _lookup_skill(name, available, workspace)
        if not skill:
            warnings.append(f"explicit skill not found: {name}")
            continue
        if not can_manual_activate(skill):
            rejected.append(_rejection(skill, score=-1000, reasons=["quarantined"], role=role))
            continue
        _append(selected, selected_keys, skill, role, stage, provider, 1000, ["explicit_request"])

    for role in roles or []:
        normalized_role = normalize_role(role)
        for name in role_bindings(config, normalized_role):
            skill = available.get(name)
            if not skill:
                warnings.append(f"binding points to missing skill: {normalized_role}={name}")
                continue
            if not can_manual_activate(skill):
                rejected.append(_rejection(skill, score=-1000, reasons=["quarantined"], role=normalized_role))
                continue
            if not _stage_compatible(skill, stage):
                continue
            score, reasons = _score_skill(skill, task=task, roles=[normalized_role], stage=stage, changed_files=changed_files)
            _append(selected, selected_keys, skill, normalized_role, stage, provider, score + 500, ["role_binding", *reasons])

    if auto and bool(config.get("auto", True)):
        for skill in available.values():
            if not _stage_compatible(skill, stage):
                continue
            score, reasons = _score_skill(skill, task=task, roles=roles or [], stage=stage, changed_files=changed_files)
            if skill.trust == "quarantined":
                rejected.append(_rejection(skill, score=-1000, reasons=["quarantined"], role=None))
                continue
            if not can_auto_activate(skill):
                if score > 0:
                    warnings.append(f"{skill.name} is visible but not auto-activated because trust={skill.trust}")
                    rejected.append(_rejection(skill, score=score - 300, reasons=[*reasons, "untrusted_penalty"], role=None))
                continue
            if _high_risk_without_approval(skill):
                rejected.append(_rejection(skill, score=score - 500, reasons=[*reasons, "high_risk_without_approval"], role=None))
                continue
            if score >= 160:
                _append(selected, selected_keys, skill, None, stage, provider, score, reasons or ["metadata_match"])
            elif score > 0:
                rejected.append(_rejection(skill, score=score, reasons=reasons, role=None))

    return SkillSelection(selected=selected, rejected=rejected, warnings=warnings, catalog_budget=catalog.budget)


def _score_skill(
    skill: SkillInfo,
    *,
    task: str,
    roles: list[str],
    stage: str | None,
    changed_files: list[str] | None,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    normalized_roles = {normalize_role(role) for role in roles}
    if normalized_roles and normalized_roles.intersection(skill.roles):
        score += 500
        reasons.append("role_binding")
    if stage and (not skill.stages or stage in skill.stages):
        if skill.stages:
            score += 250
            reasons.append("stage_match")
    if changed_files and _matches_file_pattern(skill, changed_files):
        score += 200
        reasons.append("file_pattern_match")
    task_tokens = _tokens(task)
    if task_tokens.intersection(_tokens(skill.description)):
        score += 160
        reasons.append("description_semantic_match")
    if any(keyword.lower() in task.lower() for keyword in skill.keywords + [skill.name]):
        score += 100
        reasons.append("keyword_match")
    return score, reasons


def _stage_compatible(skill: SkillInfo, stage: str | None) -> bool:
    return not stage or not skill.stages or stage in skill.stages


def _append(
    selected: list[ActivatedSkill],
    keys: set[tuple[str, str | None, str | None]],
    skill: SkillInfo,
    role: str | None,
    stage: str | None,
    provider: str,
    score: int,
    reasons: list[str],
) -> None:
    key = (skill.name, role if stage is None else None, stage)
    if key in keys:
        return
    keys.add(key)
    cleaned = _clean_reasons(reasons)
    reason = cleaned[0]
    selected.append(
        ActivatedSkill(
            skill=skill,
            role=role,
            stage=stage,
            provider=provider,
            reason=reason,
            reasons=tuple(cleaned),
            score=score,
            injection=injection_mode(provider),
            approved=True,
        )
    )


def _rejection(skill: SkillInfo, *, score: int, reasons: list[str], role: str | None) -> dict[str, object]:
    return {"name": skill.name, "score": score, "reason": _clean_reasons(reasons), "role": role, "trust": skill.trust}


def _lookup_skill(name_or_path: str, available: dict[str, SkillInfo], workspace: Path) -> SkillInfo | None:
    if name_or_path in available:
        return available[name_or_path]
    path = (workspace / name_or_path).resolve() if not Path(name_or_path).is_absolute() else Path(name_or_path)
    if path.is_dir() and (path / "SKILL.md").exists():
        return skill_from_file(path / "SKILL.md", source="task", priority=900)
    if path.name == "SKILL.md" and path.exists():
        return skill_from_file(path, source="task", priority=900)
    return None


def _parse_skill_spec(value: str) -> tuple[str | None, str]:
    if "=" in value:
        role, name = value.split("=", 1)
        return normalize_role(role), name.strip()
    return None, value.strip()


def _matches_file_pattern(skill: SkillInfo, changed_files: list[str]) -> bool:
    if not skill.file_patterns:
        return False
    return any(fnmatch.fnmatch(path, pattern) for path in changed_files for pattern in skill.file_patterns)


def _high_risk_without_approval(skill: SkillInfo) -> bool:
    permissions = skill.permissions
    return skill.risk_level == "high" and (permissions.shell or permissions.network or permissions.write_workspace or permissions.secrets)


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if len(token) >= 3}


def _clean_reasons(reasons: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for reason in reasons:
        if not reason or reason in seen:
            continue
        seen.add(reason)
        result.append(reason)
    return result or ["metadata_match"]
