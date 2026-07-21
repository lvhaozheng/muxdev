from __future__ import annotations

import pytest

from muxdev.workflows import execution_waves, load_workflow, validate_role_providers


def test_change_workflow_fans_out_independent_read_only_reviews() -> None:
    waves = execution_waves(load_workflow("change"))
    assert ["review", "security_review"] in waves
    assert waves[0] == ["plan"] and waves[-1] == ["fix"]


def test_role_provider_overrides_are_workflow_scoped() -> None:
    workflow = load_workflow("change")

    assert validate_role_providers(workflow, {"code": "codex"}) == {"code": "codex"}
    with pytest.raises(ValueError, match="unknown Agent roles"):
        validate_role_providers(workflow, {"inventor": "codex"})
    with pytest.raises(ValueError, match="not used by workflow change"):
        validate_role_providers(workflow, {"architect": "codex"})
