from __future__ import annotations

import inspect
from pathlib import Path

from muxdev.domain import StageExecutionResult
from muxdev.providers.contracts import ProviderAdapter
from muxdev.services.evidence_policy import load_evidence_policy
from muxdev.services.skills import scan_skills, validate_skill_path, verify_skill_bindings
from muxdev.storage import CORE_TABLES, ControlStore
from muxdev.workflows import load_workflow


def test_provider_has_one_execution_method_and_no_self_reported_evidence() -> None:
    public_methods = {
        name for name, value in ProviderAdapter.__dict__.items()
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert public_methods == {"execute"}
    assert "evidence" not in StageExecutionResult.__dataclass_fields__


def test_fixed_workflows_profiles_skills_and_tables(workspace: Path) -> None:
    assert {load_workflow(name).name for name in ("change", "design", "review", "test")} == {"change", "design", "review", "test"}
    assert {load_evidence_policy(workspace, workflow="change", profile=name).policy_id for name in ("lite", "standard", "strict")}
    skills = [item for item in scan_skills(workspace, include_disabled=True) if item.source == "builtin"]
    assert {item.name for item in skills} == {"default-plan", "default-code", "default-test", "default-review", "default-secure"}
    assert all(validate_skill_path(Path(item.path), strict=True)["valid"] for item in skills)
    assert all(verify_skill_bindings(workspace, item.name)["valid"] for item in skills)
    with ControlStore(workspace) as store:
        assert set(store.table_names()) == set(CORE_TABLES)


def test_builtin_skill_text_cannot_declare_gate(workspace: Path) -> None:
    forbidden = ("Delivery Standard", "Pass when", "Block when", "delivery_decision", "confidence")
    for skill in scan_skills(workspace, include_disabled=True):
        if skill.source != "builtin":
            continue
        text = Path(skill.skill_file).read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden)
