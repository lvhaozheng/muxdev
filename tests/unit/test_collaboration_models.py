from __future__ import annotations

import pytest
from pydantic import ValidationError

from muxdev.config.loader import load_config, validate_config
from muxdev.models import CliAdapterDefinition, OrchestrationPlanV1
from muxdev.services.agents import AgentRegistry


def test_builtin_agent_config_is_valid(workspace):
    config = load_config(workspace)
    result = validate_config(config)
    assert result["valid"] is True
    registry = AgentRegistry(workspace, config=config)
    assert registry.get("codex").can_orchestrate is True
    assert registry.adapter_for("codex").supports_resume is True


def test_cli_adapter_rejects_shell_text_and_unknown_placeholders():
    with pytest.raises(ValidationError):
        CliAdapterDefinition(cli_id="bad", command="tool --flag")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="unsupported CLI argv placeholder"):
        CliAdapterDefinition(cli_id="bad", command=["tool", "{shell}"])


def test_orchestration_plan_rejects_cycles():
    with pytest.raises(ValidationError, match="acyclic"):
        OrchestrationPlanV1.model_validate(
            {
                "summary": "cycle",
                "nodes": [
                    {
                        "id": "a",
                        "title": "A",
                        "brief": "A task",
                        "agent_id": "mock",
                        "role": "implementer",
                        "dependencies": ["b"],
                        "work_mode": "write",
                        "deliverables": ["a"],
                        "completion": ["done"],
                        "proof": ["artifact"],
                    },
                    {
                        "id": "b",
                        "title": "B",
                        "brief": "B task",
                        "agent_id": "mock-review",
                        "role": "reviewer",
                        "dependencies": ["a"],
                        "work_mode": "consult",
                        "deliverables": ["b"],
                        "completion": ["done"],
                        "proof": ["review"],
                    },
                ],
            }
        )
