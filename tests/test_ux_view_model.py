from __future__ import annotations

from muxdev.services.ux import build_task_ux_summary, build_ux_overview


def test_task_ux_summary_prioritizes_provider_action() -> None:
    payload = {
        "run": {"run_id": "run_ux", "task": "ship user flow", "status": "awaiting_provider_action"},
        "stages": [{"stage_id": "code", "role": "code", "status": "running", "summary": "waiting"}],
        "approvals": [],
        "provider_actions": [
            {
                "action_id": "pact_1",
                "run_id": "run_ux",
                "stage_id": "code",
                "provider": "codex",
                "kind": "cli_confirmation",
                "status": "pending",
                "prompt_text": "Apply this change? [y/N]",
                "attach_command": "muxdev attach run_ux --agent coder",
            }
        ],
        "errors": [],
        "review_blockers": [],
        "artifacts": [],
        "trace": [],
    }

    ux = build_task_ux_summary(payload)

    assert ux["user_state"] == "needs_action"
    assert ux["headline"] == "codex is waiting for your action"
    assert ux["risk"] == "medium"
    assert ux["next_actions"][0]["kind"] == "copy_command"
    assert ux["next_actions"][1]["endpoint"] == "/api/tasks/run_ux/actions/pact_1/handled-and-continue"


def test_ux_overview_collects_action_center_items() -> None:
    overview = build_ux_overview(
        daemon={"status": "running", "tasks": 1},
        tasks=[{"task_id": "run_ux", "status": "awaiting_provider_action", "pending_provider_actions": 1}],
        approvals=[{"approval_id": "appr_1", "run_id": "run_ux", "reason": "plan gate"}],
        provider_actions=[{"action_id": "pact_1", "run_id": "run_ux", "provider": "codex", "attach_command": "attach"}],
    )

    assert overview["counts"]["needs_attention"] == 1
    assert [item["kind"] for item in overview["action_center"]] == ["provider_action", "approval"]
    assert overview["headline"] == "2 item(s) need your attention"
    assert overview["current_status"]["waiting_provider_action"] == 1
    assert {column["id"] for column in overview["task_board"]} == {"todo", "running", "waiting", "needs_review", "done", "failed"}
    assert overview["task_board"][2]["tasks"][0]["task_id"] == "run_ux"
    assert "provider" in overview["filters"]
    assert "final_report" in overview["artifact_center"]["kinds"]
