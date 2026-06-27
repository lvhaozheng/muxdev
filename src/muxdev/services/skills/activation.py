"""Skill activation and compatibility resolution."""

from __future__ import annotations

from pathlib import Path

from .adapters import activated_skill_wrapper
from .events import append_skill_event
from .lock import skill_tree_hash
from .model import ActivatedSkill
from .selector import select_skills
from .validation import find_skill
from ..delivery_gate import delivery_rule_for_skill_payload


def activate_skill(
    workspace: Path,
    name: str,
    *,
    role: str | None = None,
    provider: str = "mock",
    stage: str | None = None,
    run_id: str | None = None,
    include_resources: bool = False,
) -> ActivatedSkill:
    """Load Level 2 skill instructions on demand and record activation evidence."""
    skill = find_skill(workspace, name)
    content = Path(skill.skill_file).read_text(encoding="utf-8", errors="replace")
    tree_hash = skill_tree_hash(skill)
    wrapper = activated_skill_wrapper(skill, role=role, stage=stage, provider=provider, reason="manual_activate", content=content, tree_hash=tree_hash)
    activated = ActivatedSkill(
        skill=skill,
        role=role,
        stage=stage,
        provider=provider,
        reason="manual_activate",
        reasons=("manual_activate",),
        score=1000,
        injection="native_or_passthrough" if provider in {"codex", "claude", "claude-code"} else "prompt",
        resources=_resource_manifest(skill.path) if include_resources else {},
        wrapper=wrapper,
    )
    _record_activation(workspace, activated, run_id=run_id, tree_hash=tree_hash)
    return activated


def resolve_active_skills(
    workspace: Path,
    *,
    task: str,
    roles: list[str] | None = None,
    stage: str | None = None,
    changed_files: list[str] | None = None,
    provider: str = "mock",
    explicit: list[str] | None = None,
    env: dict[str, str] | None = None,
    include_content: bool = False,
    auto: bool = True,
) -> list[dict[str, object]]:
    """Compatibility API used by older CLI/runtime code."""
    selection = select_skills(
        workspace,
        task=task,
        roles=roles,
        stage=stage,
        changed_files=changed_files,
        explicit=explicit,
        provider=provider,
        env=env,
        auto=auto,
    )
    rows: list[dict[str, object]] = []
    for item in selection.selected:
        content = Path(item.skill.skill_file).read_text(encoding="utf-8", errors="replace") if include_content else ""
        tree_hash = skill_tree_hash(item.skill)
        wrapper = activated_skill_wrapper(
            item.skill,
            role=item.role,
            stage=item.stage,
            provider=provider,
            reason=item.reason,
            content=content,
            tree_hash=tree_hash,
        )
        activated = ActivatedSkill(
            skill=item.skill,
            role=item.role,
            stage=item.stage,
            provider=provider,
            reason=item.reason,
            reasons=item.reasons,
            score=item.score,
            injection=item.injection,
            approved=item.approved,
            wrapper=wrapper if include_content else None,
        )
        if include_content:
            _record_activation(workspace, activated, run_id=None, tree_hash=tree_hash)
        payload = activated.to_dict(include_content=include_content)
        if include_content:
            rule = delivery_rule_for_skill_payload(payload)
            if rule:
                payload["delivery_rules"] = rule
                payload["delivery_rule_hash"] = rule.get("delivery_rule_hash")
        rows.append(payload)
    return rows


def _record_activation(workspace: Path, item: ActivatedSkill, *, run_id: str | None, tree_hash: str) -> None:
    append_skill_event(
        workspace,
        {
            "kind": "skill_activation",
            "run_id": run_id,
            "stage": item.stage,
            "role": item.role,
            "provider": item.provider,
            "skill": item.skill.name,
            "version": item.skill.version,
            "tree_hash": tree_hash,
            "reason": item.reason,
            "reasons": list(item.reasons),
            "trust": item.skill.trust,
            "approved": item.approved,
        },
    )


def _resource_manifest(skill_path: str) -> dict[str, list[dict[str, object]]]:
    root = Path(skill_path)
    result: dict[str, list[dict[str, object]]] = {}
    for directory in ("references", "assets", "scripts", "evals"):
        target = root / directory
        if not target.exists():
            continue
        rows: list[dict[str, object]] = []
        for path in sorted(child for child in target.rglob("*") if child.is_file()):
            rows.append({"path": str(path), "relative_path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size})
        result[directory] = rows
    return result
