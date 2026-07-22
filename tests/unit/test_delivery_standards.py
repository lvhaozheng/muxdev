from __future__ import annotations

import pytest

from muxdev.runtime.delivery_standards import build_delivery_standard


@pytest.mark.parametrize(
    ("workflow", "profile", "expected"),
    [
        ("change", "lite", ["implement", "test", "review", "accept_delivery"]),
        ("change", "standard", ["plan", "implement", "test", "review", "accept_delivery"]),
        ("change", "strict", ["plan", "implement", "test", "review", "security_review", "accept_delivery"]),
        ("design", "lite", ["design", "review", "accept_delivery"]),
        ("design", "standard", ["design", "review", "accept_delivery"]),
        ("design", "strict", ["design", "review", "accept_delivery"]),
        ("review", "lite", ["review", "accept_delivery"]),
        ("review", "standard", ["review", "accept_delivery"]),
        ("review", "strict", ["review", "accept_delivery"]),
        ("test", "lite", ["test", "review", "accept_delivery"]),
        ("test", "standard", ["test_plan", "test", "review", "accept_delivery"]),
        ("test", "strict", ["test_plan", "test", "review", "accept_delivery"]),
    ],
)
def test_delivery_baseline_is_compact_for_every_workflow_profile(
    workflow: str,
    profile: str,
    expected: list[str],
) -> None:
    standard = build_delivery_standard(workflow, profile)

    assert standard["schema_version"] == "muxdev.delivery-standard.v2"
    assert standard["baseline_version"] == "sdlc-v2"
    assert [item["stage_id"] for item in standard["stages"]] == expected
    assert all(
        item["source"] == "baseline" and item["required"] is True
        for stage in standard["stages"]
        for item in stage["baseline_items"]
    )


def test_custom_standard_accepts_only_enabled_stages_and_frozen_commands() -> None:
    valid = build_delivery_standard("change", "lite", [{
        "id": "diff-clean",
        "stage_id": "test",
        "deliverable": "干净的差异",
        "completion": "差异必须通过完整性检查",
        "proof": "冻结 Runtime check",
        "verifier": {"type": "runtime_check", "command_id": "diff-integrity"},
    }])
    assert valid["custom_items"][0]["required"] is True
    assert valid["custom_items"][0]["completion"] == "差异必须通过完整性检查"

    with pytest.raises(ValueError, match="not a frozen workflow command"):
        build_delivery_standard("change", "lite", [{
            "stage_id": "test",
            "text": "运行任意命令",
            "verifier": {"type": "check", "command_id": "shell-input"},
        }])
    with pytest.raises(ValueError, match="stage is not enabled"):
        build_delivery_standard("change", "lite", [{
            "stage_id": "security_review",
            "text": "进行安全评审",
        }])
    with pytest.raises(ValueError, match="cannot exceed 300"):
        build_delivery_standard("change", "lite", [{
            "stage_id": "review",
            "text": "x" * 301,
        }])
    with pytest.raises(ValueError, match="more than 10"):
        build_delivery_standard("change", "lite", [
            {"stage_id": "review", "text": f"规则 {index}"}
            for index in range(11)
        ])


def test_baseline_fields_cannot_be_submitted_as_conversation_rules() -> None:
    with pytest.raises(ValueError, match="cannot be edited"):
        build_delivery_standard("change", "lite", [{
            "stage_id": "review",
            "text": "伪装为内置规则",
            "source": "baseline",
        }])
    with pytest.raises(ValueError, match="always required"):
        build_delivery_standard("change", "lite", [{
            "stage_id": "review",
            "text": "可选规则",
            "required": False,
        }])
