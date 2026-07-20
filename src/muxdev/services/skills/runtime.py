"""Resolve stage-declared Skills without allowing them to expand authority."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .discovery import scan_skills


def resolve_stage_skills(
    workspace: Path,
    declarations: Iterable[str],
    *,
    role: str | None,
    stage_id: str,
    allow_write: bool,
    allow_shell: bool,
) -> tuple[dict[str, object], ...]:
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
        if skill.permissions.write_workspace and not allow_write:
            raise PermissionError(f"Skill {name} requests workspace write outside stage authority")
        if skill.permissions.shell and not allow_shell:
            raise PermissionError(f"Skill {name} requests shell outside stage authority")
        if skill.permissions.network or skill.permissions.secrets or skill.permissions.mcp:
            raise PermissionError(f"Skill {name} requests authority not granted by the compact workflow")
        resolved.append(skill.to_dict(include_content=True))
    return tuple(resolved)


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
