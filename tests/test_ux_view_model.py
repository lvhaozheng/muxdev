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


def test_design_provider_action_is_labeled_as_provider_native_question() -> None:
    payload = {
        "run": {"run_id": "run_design", "task": "设计贪吃蛇游戏", "status": "awaiting_provider_action"},
        "stages": [{"stage_id": "design_brief", "role": "architect", "status": "running", "summary": "waiting"}],
        "approvals": [],
        "provider_actions": [
            {
                "action_id": "pact_design",
                "run_id": "run_design",
                "stage_id": "design_brief",
                "provider": "codex",
                "kind": "cli_confirmation",
                "status": "pending",
                "prompt_text": "请确认目标用户和视觉风格",
            }
        ],
        "errors": [],
        "review_blockers": [],
        "artifacts": [],
        "trace": [],
    }

    ux = build_task_ux_summary(payload)

    assert ux["headline"] == "codex 需要你确认设计风格"
    assert "Provider 在设计阶段通过自身 CLI/session 请求" in ux["why"]


def test_ux_overview_collects_action_center_items() -> None:
    overview = build_ux_overview(
        daemon={"status": "running", "tasks": 1},
        tasks=[{"task_id": "run_ux", "status": "awaiting_provider_action", "pending_provider_actions": 1}],
        approvals=[
            {
                "approval_id": "appr_1",
                "run_id": "run_ux",
                "reason": "plan gate",
                "subject_hash": "sha256:plan",
                "subject_json": '{"type":"plan","stage":"design","plan_hash":"sha256:plan"}',
            }
        ],
        provider_actions=[{"action_id": "pact_1", "run_id": "run_ux", "provider": "codex", "attach_command": "attach"}],
    )

    assert overview["counts"]["needs_attention"] == 1
    assert [item["kind"] for item in overview["action_center"]] == ["provider_action", "approval"]
    assert overview["headline"] == "2 item(s) need your attention"
    assert overview["current_status"]["waiting_provider_action"] == 1
    assert overview["action_center"][1]["subject_hash"] == "sha256:plan"
    assert "plan_hash=sha256:plan" in overview["action_center"][1]["subject_summary"]
    assert {column["id"] for column in overview["task_board"]} == {"todo", "running", "waiting", "needs_review", "done", "failed"}
    assert overview["task_board"][2]["tasks"][0]["task_id"] == "run_ux"
    assert "provider" in overview["filters"]
    assert "final_report" in overview["artifact_center"]["kinds"]
    assert "test_report" in overview["artifact_center"]["kinds"]


def test_ux_overview_surfaces_missing_completed_deliverables() -> None:
    overview = build_ux_overview(
        daemon={"status": "running", "tasks": 1},
        tasks=[
            {
                "task_id": "run_missing",
                "task": "ship docs",
                "status": "completed",
                "deliverable_status": {"missing": ["docs_report"], "required": ["docs_report"], "ready": []},
            }
        ],
        approvals=[],
        provider_actions=[],
    )

    assert overview["counts"]["needs_attention"] == 1
    assert overview["action_center"][0]["kind"] == "missing_deliverable"
    assert "docs_report" in overview["action_center"][0]["why"]


def test_ux_overview_labels_design_provider_action() -> None:
    overview = build_ux_overview(
        daemon={"status": "running", "tasks": 1},
        tasks=[
            {
                "task_id": "run_design",
                "task": "设计贪吃蛇游戏",
                "status": "awaiting_provider_action",
                "pending_provider_actions": 1,
                "current_stage": "design_brief",
            }
        ],
        approvals=[],
        provider_actions=[
            {
                "action_id": "pact_design",
                "run_id": "run_design",
                "stage_id": "design_brief",
                "provider": "codex",
                "prompt_text": "请确认目标用户和视觉风格",
            }
        ],
    )

    item = overview["action_center"][0]
    assert item["kind"] == "provider_action"
    assert [row["kind"] for row in overview["action_center"]] == ["provider_action"]
    assert item["headline"] == "codex 需要你确认设计风格"
    assert "设计阶段" in item["why"]


def test_ux_overview_surfaces_recovery_items_with_error_reason() -> None:
    overview = build_ux_overview(
        daemon={"status": "running", "tasks": 1},
        tasks=[
            {
                "task_id": "run_failed",
                "task": "fix dashboard",
                "status": "blocked",
                "errors": 1,
                "error_summary": {"stage_id": "code", "type": "provider_exit", "message": "temporary network error"},
                "project_id": "project_1",
                "project_name": "dashboard",
                "project_path": "/tmp/dashboard",
                "recover_endpoint": "/api/tasks/run_failed/continue",
                "report_endpoint": "/api/tasks/run_failed/report",
                "rollback_endpoint": "/api/tasks/run_failed/rollback",
            }
        ],
        approvals=[],
        provider_actions=[],
    )

    assert overview["action_center"][0]["kind"] == "recovery"
    assert overview["action_center"][0]["endpoint"] == "/api/tasks/run_failed/continue"
    assert "temporary network error" in overview["action_center"][0]["why"]
    assert overview["action_center"][0]["task_title"] == "fix dashboard"
    assert overview["action_center"][0]["project_name"] == "dashboard"
    assert overview["action_center"][0]["run_id"] == "run_failed"
    assert overview["action_center"][0]["stage_id"] == "code"
    assert overview["current_status"]["stuck"] == 1


def test_ux_overview_recovery_context_identifies_task_without_error_message() -> None:
    overview = build_ux_overview(
        daemon={"status": "running", "tasks": 1},
        tasks=[
            {
                "task_id": "run_aborted",
                "task_title": "design snake game",
                "status": "aborted",
                "errors": 0,
                "project_id": "project_game",
                "project_name": "game",
                "project_path": "/tmp/game",
                "current_stage": "design_brief",
            }
        ],
        approvals=[],
        provider_actions=[],
    )

    item = overview["action_center"][0]
    assert item["kind"] == "recovery"
    assert item["why"] == "The task stopped before normal delivery."
    assert item["task_title"] == "design snake game"
    assert item["project_name"] == "game"
    assert item["run_id"] == "run_aborted"
    assert item["stage_id"] == "design_brief"


def test_task_ux_summary_surfaces_plan_feedback_and_elapsed_time() -> None:
    payload = {
        "run": {"run_id": "run_plan", "task": "design snake game", "status": "running"},
        "stages": [{"stage_id": "plan", "role": "plan", "status": "running", "summary": "drafting", "elapsed_seconds": 3700}],
        "summary": {"current_activity": "provider codex attempt 1 on plan", "current_stage_elapsed_seconds": 3700},
        "approvals": [],
        "provider_actions": [],
        "errors": [],
        "review_blockers": [],
        "artifacts": [],
        "trace": [],
    }

    ux = build_task_ux_summary(payload)

    assert ux["next_actions"][0]["kind"] == "plan_feedback"
    assert ux["next_actions"][0]["optional"] is True
    assert "--run-id run_plan" in ux["next_actions"][0]["command"]
    assert "1h 1m" in ux["why"]


def test_task_ux_summary_reconciles_awaiting_approval_without_pending_record() -> None:
    payload = {
        "run": {"run_id": "run_orphan", "task": "fix health endpoint", "status": "awaiting_approval"},
        "stages": [{"stage_id": "approve_plan", "role": "plan", "status": "running", "summary": "waiting"}],
        "summary": {"current_activity": "running stage approve_plan", "current_stage_elapsed_seconds": 14400},
        "approvals": [],
        "provider_actions": [],
        "errors": [],
        "review_blockers": [],
        "artifacts": [],
        "trace": [],
    }

    ux = build_task_ux_summary(payload)

    assert ux["user_state"] == "needs_attention"
    assert ux["headline"] == "Approval state needs reconciliation"
    assert ux["next_actions"][0]["kind"] == "continue_task"
    assert ux["next_actions"][0]["endpoint"] == "/api/tasks/run_orphan/continue"
    assert all(item["kind"] != "plan_feedback" for item in ux["next_actions"])


def test_task_ux_summary_reconciles_awaiting_provider_action_without_pending_record() -> None:
    payload = {
        "run": {"run_id": "run_provider_orphan", "task": "verify health endpoint", "status": "awaiting_provider_action"},
        "stages": [{"stage_id": "test", "role": "test", "status": "running", "summary": "waiting for provider action"}],
        "summary": {"current_activity": "running stage test", "current_stage_elapsed_seconds": 900},
        "approvals": [],
        "provider_actions": [{"action_id": "pact_1", "status": "handled", "run_id": "run_provider_orphan"}],
        "errors": [],
        "review_blockers": [],
        "artifacts": [],
        "trace": [],
    }

    ux = build_task_ux_summary(payload)

    assert ux["user_state"] == "needs_attention"
    assert ux["headline"] == "Provider action state needs reconciliation"
    assert ux["next_actions"][0]["kind"] == "continue_task"
    assert ux["next_actions"][0]["endpoint"] == "/api/tasks/run_provider_orphan/continue"
    assert all(item["kind"] != "plan_feedback" for item in ux["next_actions"])


def test_task_ux_summary_surfaces_design_document_deliverable() -> None:
    payload = {
        "run": {"run_id": "run_design", "task": "design snake game", "status": "completed"},
        "stages": [{"stage_id": "final_design_review", "role": "review", "status": "completed", "summary": "done"}],
        "summary": {},
        "approvals": [],
        "provider_actions": [],
        "errors": [],
        "review_blockers": [],
        "artifacts": [
            {
                "kind": "project_design_doc",
                "name": "Design Document",
                "path": "docs/design/design.md",
            }
        ],
        "trace": [],
    }

    ux = build_task_ux_summary(payload)

    assert ux["deliverables"][0] == {
        "kind": "project_design_doc",
        "label": "Design document",
        "path": "docs/design/design.md",
        "ready": True,
    }


def test_ux_overview_surfaces_planning_feedback_as_optional_action() -> None:
    overview = build_ux_overview(
        daemon={"status": "running", "tasks": 1},
        tasks=[
            {
                "task_id": "run_plan",
                "status": "running",
                "current_stage": "plan",
                "current_activity": "provider codex attempt 1 on plan",
                "pending_approvals": 0,
                "pending_provider_actions": 0,
            }
        ],
        approvals=[],
        provider_actions=[],
    )

    assert overview["action_center"] == []
    assert overview["counts"]["needs_attention"] == 0
    assert overview["headline"] == "1 task(s) are running"
    assert overview["optional_actions"][0]["kind"] == "plan_feedback"
    assert overview["optional_actions"][0]["optional"] is True
    assert "--run-id run_plan" in overview["optional_actions"][0]["command"]


def test_ux_overview_reconciles_awaiting_approval_without_pending_record() -> None:
    overview = build_ux_overview(
        daemon={"status": "running", "tasks": 1},
        tasks=[
            {
                "task_id": "run_orphan",
                "status": "awaiting_approval",
                "current_stage": "approve_plan",
                "current_activity": "running stage approve_plan",
                "pending_approvals": 0,
                "pending_provider_actions": 0,
                "errors": 0,
                "recover_endpoint": "/api/tasks/run_orphan/continue",
            }
        ],
        approvals=[],
        provider_actions=[],
    )

    assert overview["counts"]["needs_attention"] == 1
    assert overview["action_center"][0]["kind"] == "approval_reconcile"
    assert overview["action_center"][0]["endpoint"] == "/api/tasks/run_orphan/continue"


def test_ux_overview_reconciles_awaiting_provider_action_without_pending_record() -> None:
    overview = build_ux_overview(
        daemon={"status": "running", "tasks": 1},
        tasks=[
            {
                "task_id": "run_provider_orphan",
                "status": "awaiting_provider_action",
                "current_stage": "test",
                "current_activity": "running stage test",
                "pending_approvals": 0,
                "pending_provider_actions": 0,
                "errors": 0,
                "recover_endpoint": "/api/tasks/run_provider_orphan/continue",
            }
        ],
        approvals=[],
        provider_actions=[],
    )

    assert overview["counts"]["needs_attention"] == 1
    assert overview["action_center"][0]["kind"] == "provider_action_reconcile"
    assert overview["action_center"][0]["endpoint"] == "/api/tasks/run_provider_orphan/continue"
