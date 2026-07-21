"""Resolve stage-declared Skills without allowing them to expand authority."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...domain import CapabilityGrant
from .discovery import scan_skills
from .lock import verify_skill_lock
from .model import SkillInfo


def resolve_stage_skills(
    workspace: Path,
    declarations: Iterable[str],
    *,
    role: str | None,
    stage_id: str,
    allow_write: bool,
    allow_shell: bool,
    grant: CapabilityGrant | None = None,
    profile: str = "standard",
) -> tuple[dict[str, object], ...]:
    grant = grant or CapabilityGrant(write_workspace=allow_write, shell=allow_shell)
    available = {item.name: item for item in scan_skills(workspace, include_disabled=True)}
    resolved: list[dict[str, object]] = []
    for declaration in declarations:
        name = declaration.split("=", 1)[-1].strip()
        skill = available.get(name)
        if not skill:
            raise ValueError(f"workflow declares missing Skill: {name}")
        if skill.disabled or skill.validation_errors:
            raise ValueError(f"Skill is disabled or invalid: {name}")
        if skill.roles and role not in skill.roles:
            raise ValueError(f"Skill {name} is not activated for role {role}")
        if skill.stages and stage_id not in skill.stages:
            raise ValueError(f"Skill {name} is not activated for stage {stage_id}")
        if skill.permissions.write_workspace and not grant.write_workspace:
            raise PermissionError(f"Skill {name} requests workspace write outside stage authority")
        if skill.permissions.shell and not grant.shell:
            raise PermissionError(f"Skill {name} requests shell outside stage authority")
        if skill.permissions.network:
            raise PermissionError(
                f"Skill {name} requests authority not granted: direct network access is forbidden; "
                "declare a read-only MCP tool"
            )
        if skill.permissions.secrets:
            raise PermissionError(f"Skill {name} cannot receive Secret values")
        requested_mcp = set(skill.permissions.mcp)
        if any("*" in item or "/" not in item for item in requested_mcp):
            raise PermissionError(f"Skill {name} MCP permissions must use exact server/tool references")
        if not requested_mcp.issubset(grant.mcp_tools):
            raise PermissionError(
                f"Skill {name} requests MCP tools outside stage authority: {sorted(requested_mcp - set(grant.mcp_tools))}"
            )
        _require_trusted_skill(workspace, skill, profile=profile)
        resolved.append(skill.to_dict(include_content=True))
    return tuple(resolved)


def _require_trusted_skill(workspace: Path, skill: SkillInfo, *, profile: str) -> None:
    if skill.trust in {"untrusted", "needs_review", "quarantined"}:
        raise PermissionError(f"Skill {skill.name} is not trusted: {skill.trust}")
    if skill.trust == "builtin_trusted":
        return
    lock = verify_skill_lock(workspace, name=skill.name)
    rows = lock.get("skills") if isinstance(lock.get("skills"), list) else []
    status = next((str(item.get("status")) for item in rows if item.get("name") == skill.name), "unlocked")
    if status != "valid":
        raise PermissionError(f"Skill {skill.name} lock is not valid: {status}")


def verify_skill_bindings(workspace: Path, name: str) -> dict[str, object]:
    from ...workflows import load_workflow

    errors: list[str] = []
    bindings: list[dict[str, object]] = []
    expected = {
        ("default-plan", "plan"): "PlanResult",
        ("default-plan", "architect"): "PlanResult",
        ("default-code", "code"): "ChangeResult",
        ("default-test", "test_strategy"): "PlanResult",
        ("default-test", "test"): "TestResult",
        ("default-review", "review"): "ReviewResult",
        ("default-secure", "secure"): "ReviewResult",
    }
    for workflow_name in ("change", "design", "review", "test"):
        for stage in load_workflow(workflow_name).stages:
            declared = [item.split("=", 1)[-1] for item in stage.default_skills]
            if name not in declared:
                continue
            try:
                resolve_stage_skills(
                    workspace,
                    [name],
                    role=stage.role,
                    stage_id=stage.id,
                    allow_write=stage.allow_write,
                    allow_shell=stage.allow_shell,
                )
            except (ValueError, PermissionError) as exc:
                errors.append(f"{workflow_name}/{stage.id}: {exc}")
            required_schema = expected.get((name, stage.role or ""))
            if required_schema and stage.output_schema != required_schema:
                errors.append(
                    f"{workflow_name}/{stage.id}: expected output schema {required_schema}, found {stage.output_schema}"
                )
            bindings.append({"workflow": workflow_name, "stage": stage.id, "role": stage.role, "schema": stage.output_schema})
    return {"valid": not errors, "errors": errors, "bindings": bindings}
