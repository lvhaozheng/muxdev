from __future__ import annotations

from muxdev.models import WorkflowStage
from muxdev.services.prompt_templates import render_stage_prompt


def test_stage_prompt_composes_role_and_schema_instructions() -> None:
    prompt = render_stage_prompt(
        "add rate limiting",
        workflow="dev",
        stage=WorkflowStage(id="plan", role="architect", read_only=True, output_schema="PlanArtifact"),
    )

    assert prompt.role_key == "plan"
    assert "Workflow: dev" in prompt.text
    assert "Role: architect" in prompt.text
    assert "Shape the solution boundaries" in prompt.text
    assert "Return a concise plan" in prompt.text
    assert "delivery decision" in prompt.text
    assert "evidence" in prompt.text
    assert "gaps" in prompt.text


def test_stage_prompt_allows_workflow_stage_override() -> None:
    prompt = render_stage_prompt(
        "ship docs",
        workflow="docs",
        stage=WorkflowStage(id="docs", role="docs", allow_write=True, prompt="Use the product glossary."),
    )

    assert "allow_write" in prompt.text
    assert "Use the product glossary." in prompt.text


def test_output_contracts_include_delivery_evidence_and_gaps() -> None:
    for schema in ("PlanArtifact", "TestResult", "ReviewResult"):
        prompt = render_stage_prompt(
            "verify delivery standard",
            workflow="dev",
            stage=WorkflowStage(id=schema.lower(), role="review", output_schema=schema),
        )
        lowered = prompt.text.lower()

        assert "delivery decision" in lowered
        assert "evidence" in lowered
        assert "gap" in lowered or "missing evidence" in lowered


def test_design_lite_prompt_requires_complete_design_md() -> None:
    prompt = render_stage_prompt(
        "design a snake game",
        workflow="design-lite",
        stage=WorkflowStage(id="design_brief", role="architect", read_only=True, output_schema="PlanArtifact"),
    )
    lowered = prompt.text.lower()

    assert "docs/design/design.md" in prompt.text
    assert "complete single-file design document" in lowered
    assert "test strategy" in lowered
    assert "no implementation files" in lowered
