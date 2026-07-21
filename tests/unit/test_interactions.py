from __future__ import annotations

from muxdev.runtime.interactions import normalize_interaction_request


def test_low_risk_question_defaults_after_one_minute() -> None:
    question = normalize_interaction_request({
        "question": "列表默认按哪个字段排序？",
        "options": [
            {"id": "updated", "label": "更新时间", "recommended": True},
            {"id": "created", "label": "创建时间"},
        ],
    })

    assert question["blocking"] is False
    assert question["timeout_seconds"] == 60
    assert question["timeout_action"] == "select_recommended"
    assert question["recommended_option_id"] == "updated"
    assert question["expires_at"]


def test_sensitive_question_never_uses_a_timeout_default() -> None:
    question = normalize_interaction_request({
        "question": "是否删除现有数据并关闭交付门禁？",
        "options": ["继续", "取消"],
        "blocking": False,
        "timeout_seconds": 1,
    })

    assert question["risk"] == "high"
    assert question["blocking"] is True
    assert question["timeout_seconds"] == 0
    assert question["timeout_action"] == "wait"
    assert question["expires_at"] is None
