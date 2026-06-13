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


def test_stage_prompt_allows_workflow_stage_override() -> None:
    prompt = render_stage_prompt(
        "ship docs",
        workflow="docs",
        stage=WorkflowStage(id="docs", role="docs", allow_write=True, prompt="Use the product glossary."),
    )

    assert "allow_write" in prompt.text
    assert "Use the product glossary." in prompt.text
