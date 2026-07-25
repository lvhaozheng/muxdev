from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from muxdev.api import create_app
from muxdev.domain import StageExecutionResult
from muxdev.providers.mock import MockProvider
from muxdev.runtime import ConversationService, RunEngine
from muxdev.runtime.workspace import WorkspaceConflictError
from muxdev.models import ExecutedCheck
from muxdev.models.evidence import canonical_hash


def test_conversation_defers_workspace_delivery_until_acceptance(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("add a conversation marker", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]

    result = service.start_work(conversation_id)
    candidate = result["candidates"][-1]

    assert result["conversation"]["status"] == "awaiting_acceptance"
    assert candidate["status"] == "verified"
    assert result["active_delivery"]["status_label"] == "已验证，可交付"
    assert not (workspace / "muxdev_mock_change.txt").exists()

    service.accept_delivery(conversation_id, candidate["candidate_id"])
    delivered = service.get(conversation_id)

    assert delivered["conversation"]["status"] == "idle"
    assert delivered["candidates"][-1]["status"] == "accepted"
    assert delivered["active_delivery"]["status_label"] == "已写入项目"
    assert (workspace / "muxdev_mock_change.txt").is_file()
    assert delivered["integrity"]["valid"] is True
    engine.store.close()


def test_discussion_preserves_verified_candidate_and_change_invalidates_it(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("prepare a marker", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    first = service.start_work(conversation_id)
    candidate_id = first["candidates"][-1]["candidate_id"]

    service.add_message(conversation_id, "为什么这样实现？", intent="discuss")
    assert service.get(conversation_id)["candidates"][-1]["status"] == "verified"

    service.add_message(conversation_id, "再补充一条确定性说明", intent="change")
    changed = service.get(conversation_id)
    assert changed["conversation"]["conversation_id"] == conversation_id
    assert next(item for item in changed["candidates"] if item["candidate_id"] == candidate_id)["status"] == "invalidated"
    engine.store.close()


def test_blocked_run_keeps_conversation_open_with_one_next_state(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("standard delivery without reviewer", profile="standard")
    result = service.start_work(created["conversation"]["conversation_id"])

    assert result["conversation"]["status"] == "needs_user"
    assert result["candidates"][-1]["status"] == "failed"
    assert any(item["type"] == "delivery.needs_user" for item in engine.store.conversation_events(
        created["conversation"]["conversation_id"]
    ))
    engine.store.close()


def test_verified_delivery_stays_immutable_across_a_second_revision(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("deliver the first version", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    first = service.start_work(conversation_id)["candidates"][-1]
    service.accept_delivery(conversation_id, first["candidate_id"])

    service.add_message(conversation_id, "继续完成第二个修订", intent="change")
    second_result = service.start_work(conversation_id)
    second = second_result["candidates"][-1]

    assert first["candidate_id"] != second["candidate_id"]
    assert second_result["conversation"]["conversation_id"] == conversation_id
    assert next(item for item in second_result["candidates"] if item["candidate_id"] == first["candidate_id"])["status"] == "accepted"
    assert second["parent_candidate_id"] == first["candidate_id"]
    engine.store.close()


def test_workspace_drift_prevents_accepting_stale_candidate(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("prepare a conflict-safe delivery", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    candidate = service.start_work(conversation_id)["candidates"][-1]
    (workspace / "muxdev_mock_change.txt").write_text("user changed this locally", encoding="utf-8")

    with pytest.raises(WorkspaceConflictError, match="workspace changed since run start"):
        service.accept_delivery(conversation_id, candidate["candidate_id"])

    assert service.get(conversation_id)["conversation"]["status"] == "needs_user"
    engine.store.close()


def test_conversation_http_surface_supports_messages_and_actions(workspace) -> None:
    client = TestClient(create_app(workspace))
    response = client.post("/api/v1/conversations", json={
        "goal": "build through a conversation",
        "profile": "lite",
        "auto_start": False,
    })
    assert response.status_code == 201
    conversation_id = response.json()["conversation"]["conversation_id"]

    message = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={
        "content": "先解释验收边界",
        "intent": "discuss",
        "auto_start": False,
    })
    assert message.status_code == 202
    assert message.json()["queued"] is False

    action = client.post(f"/api/v1/conversations/{conversation_id}/actions", json={
        "action": "verify",
        "payload": {},
    })
    assert action.status_code == 202
    detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert detail["conversation"]["status"] == "awaiting_acceptance"
    assert detail["deliveries"][0]["status"] == "verified"
    assert detail["delivery_standard"]["schema_version"] == "muxdev.delivery-standard.v2"
    assert detail["active_delivery"]["status_label"] == "已验证，可交付"
    assert detail["active_delivery"]["can_accept"] is True
    assert set(detail["active_delivery"]) >= {
        "changed_files", "standard_summary", "blocking_items", "evidence_integrity"
    }


def test_conversation_actions_are_idempotent_and_events_resume_by_sequence(workspace) -> None:
    client = TestClient(create_app(workspace))
    created = client.post("/api/v1/conversations", json={
        "goal": "exercise idempotent conversation controls",
        "profile": "lite",
        "auto_start": False,
    }).json()
    conversation_id = created["conversation"]["conversation_id"]
    payload = {"action": "close", "payload": {}, "idempotency_key": "same-device-action-001"}

    first = client.post(f"/api/v1/conversations/{conversation_id}/actions", json=payload)
    second = client.post(f"/api/v1/conversations/{conversation_id}/actions", json=payload)
    assert first.json()["status"] == "completed"
    assert second.json()["status"] == "duplicate"

    events = client.get(f"/api/v1/conversations/{conversation_id}/events").json()
    cursor = events[0]["sequence"]
    resumed = client.get(
        f"/api/v1/conversations/{conversation_id}/events",
        headers={"last-event-id": str(cursor)},
    ).json()
    assert resumed and all(item["sequence"] > cursor for item in resumed)


def test_conversation_builds_role_team_and_projects_worker_events_once(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create(
        "exercise the fixed Agent team",
        profile="lite",
        role_providers={"code": "mock", "test": "mock", "review": "mock"},
    )
    conversation_id = created["conversation"]["conversation_id"]

    result = service.start_work(conversation_id)
    events = engine.store.conversation_events(conversation_id)
    worker_events = [
        item for item in events
        if item["type"] == "run.event"
        and str(item["payload"].get("source_type") or "").startswith("worker.")
    ]
    run_id = result["conversation"]["active_run_id"]

    assert result["team"]["max_parallel"] == 4
    assert {item["role"] for item in result["team"]["workers"]} >= {
        "plan", "code", "test", "review"
    }
    assert {item["provider"] for item in result["team"]["workers"]} == {"mock"}
    assert worker_events
    assert len({item["run_event_id"] for item in worker_events}) == len(worker_events)
    assert engine.store.sync_run_events(conversation_id, run_id) == 0
    contract = result["contracts"][-1]
    assert contract["policy"]["role_providers"]["review"] == "mock"
    engine.store.close()


def test_conversation_api_rejects_invalid_role_provider_override(workspace) -> None:
    response = TestClient(create_app(workspace)).post("/api/v1/conversations", json={
        "goal": "invalid role",
        "profile": "lite",
        "role_providers": {"inventor": "mock"},
        "auto_start": False,
    })

    assert response.status_code == 422
    assert "unknown Agent roles" in response.json()["detail"]


def test_unavailable_role_provider_fails_preflight_with_configuration_action(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create(
        "reject an unavailable role provider",
        profile="lite",
        role_providers={"code": "missing-provider-test"},
    )

    result = service.start_work(created["conversation"]["conversation_id"])

    assert result["conversation"]["status"] == "needs_user"
    assert result["recovery"]["primary_failure"]["code"] == "provider_configuration_invalid"
    assert "missing-provider-test" in " ".join(
        result["recovery"]["primary_failure"]["details"]
    )
    assert [item["action"] for item in result["recovery"]["next_actions"]] == ["inspect"]
    engine.store.close()


def test_provider_exit_has_structured_redacted_recovery_details(
    workspace, monkeypatch
) -> None:
    class InvalidCommandProvider:
        def execute(self, stage_input):
            if stage_input.stage_id != "implement":
                return MockProvider().execute(stage_input)
            return StageExecutionResult(
                artifact_name="provider.log",
                content="",
                summary="provider failed",
                stage_id="implement",
                provider="mock",
                status="failed",
                returncode=1,
                stderr_content="sk-SECRET123 invalid argument --broken",
            )

    monkeypatch.setattr(
        "muxdev.runtime.engine.get_runtime_provider",
        lambda *_args, **_kwargs: InvalidCommandProvider(),
    )
    monkeypatch.setattr(RunEngine, "_fallback_provider", lambda *_args, **_kwargs: None)
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("show a useful provider failure", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]

    result = service.start_work(conversation_id)
    failed = next(
        item for item in reversed(engine.store.conversation_events(conversation_id))
        if item["type"] == "delivery.needs_user"
    )["payload"]

    assert result["conversation"]["status"] == "needs_user"
    assert failed["failure"]["stage_id"] == "implement"
    assert failed["failure"]["provider"] == "mock"
    assert failed["failure"]["returncode"] == 1
    assert failed["failure"]["code"] == "provider_command_invalid"
    assert [item["action"] for item in failed["next_actions"]] == ["inspect"]
    assert "sk-SECRET123" not in json.dumps(failed, ensure_ascii=False)
    engine.store.close()


def test_recovery_exception_returns_conversation_to_needs_user(workspace, monkeypatch) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("finish before recovery exception", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    service.start_work(conversation_id)

    def fail_resume(*_args, **_kwargs):
        raise RuntimeError("provider recovery crashed with sk-SECRET123")

    monkeypatch.setattr(engine, "resume", fail_resume)
    with pytest.raises(RuntimeError, match="provider recovery crashed"):
        service.recover(conversation_id)

    detail = service.get(conversation_id)
    assert detail["conversation"]["status"] == "needs_user"
    assert detail["recovery"]["primary_failure"]["code"] == "background_operation_failed"
    serialized = json.dumps(engine.store.conversation_events(conversation_id), ensure_ascii=False)
    assert "sk-SECRET123" not in serialized
    assert "recovery.failed" in serialized
    engine.store.close()


def test_non_blocking_agent_question_defaults_then_reruns_stage(workspace, monkeypatch) -> None:
    calls: dict[str, int] = {}
    monkeypatch.setattr("muxdev.runtime.interactions.DEFAULT_TIMEOUT_SECONDS", 1)

    class AskingProvider:
        def execute(self, stage_input):
            calls[stage_input.stage_id] = calls.get(stage_input.stage_id, 0) + 1
            if stage_input.stage_id == "implement" and calls[stage_input.stage_id] == 1:
                question = {
                    "question": "默认使用哪种文件名？",
                    "options": [
                        {"id": "recommended", "label": "推荐名称", "recommended": True},
                        {"id": "short", "label": "短名称"},
                    ],
                    "timeout_seconds": 1,
                }
                return StageExecutionResult(
                    artifact_name="question.json",
                    content=json.dumps({"interaction_request": question}, ensure_ascii=False),
                    summary="clarification required",
                    stage_id=stage_input.stage_id,
                    provider="mock",
                    interaction_requests=(question,),
                )
            return MockProvider().execute(stage_input)

    monkeypatch.setattr(
        "muxdev.runtime.engine.get_runtime_provider",
        lambda *_args, **_kwargs: AskingProvider(),
    )
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("complete after a safe default", profile="lite")

    result = service.start_work(created["conversation"]["conversation_id"])
    interaction = result["interactions"][0]
    response = json.loads(interaction["response"])

    assert result["conversation"]["status"] == "awaiting_acceptance"
    assert interaction["status"] == "responded"
    assert response == {"option_id": "recommended", "defaulted": True}
    assert calls["implement"] == 2
    assert any(
        event["type"] == "interaction.responded" and event["payload"].get("defaulted")
        for event in engine.store.events(result["conversation"]["active_run_id"])
    )
    engine.store.close()


def test_high_risk_agent_question_pauses_until_explicit_choice(workspace, monkeypatch) -> None:
    calls: dict[str, int] = {}

    class AskingProvider:
        def execute(self, stage_input):
            calls[stage_input.stage_id] = calls.get(stage_input.stage_id, 0) + 1
            if stage_input.stage_id == "implement" and calls[stage_input.stage_id] == 1:
                question = {
                    "question": "是否删除旧数据后继续？",
                    "options": [
                        {"id": "keep", "label": "保留数据", "recommended": True},
                        {"id": "delete", "label": "删除数据"},
                    ],
                    "timeout_seconds": 1,
                }
                return StageExecutionResult(
                    artifact_name="question.json",
                    content=json.dumps({"interaction_request": question}, ensure_ascii=False),
                    summary="explicit confirmation required",
                    stage_id=stage_input.stage_id,
                    provider="mock",
                    interaction_requests=(question,),
                )
            return MockProvider().execute(stage_input)

    monkeypatch.setattr(
        "muxdev.runtime.engine.get_runtime_provider",
        lambda *_args, **_kwargs: AskingProvider(),
    )
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("pause for destructive ambiguity", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]

    paused = service.start_work(conversation_id)
    interaction = paused["interactions"][0]
    assert paused["conversation"]["status"] == "needs_user"
    assert paused["progress"]["state"] == "waiting_user"
    assert interaction["blocking"] is True
    assert interaction["expires_at"] is None

    answer = service.respond_interaction(
        conversation_id, interaction["interaction_id"], option_id="keep"
    )
    assert answer["resume_required"] is True
    completed = service.resume_interaction(conversation_id)

    assert completed["conversation"]["status"] == "awaiting_acceptance"
    assert calls["implement"] == 2
    engine.store.close()


def test_stage_standards_are_versioned_and_projected_with_downstream_impact(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("show stage delivery standards", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]

    revised = service.revise_contract(
        conversation_id,
        {"stage_standards": {"implement": ["不得改变公开 API"]}},
    )
    detail = service.get(conversation_id)
    implement = next(
        item for item in detail["stage_deliveries"] if item["stage_id"] == "implement"
    )

    assert revised["version"] == 2
    assert revised["policy"]["stage_standards"]["implement"] == ["不得改变公开 API"]
    assert revised["policy"]["revision"]["affected_stages"] == [
        "implement", "test", "review", "security_review", "fix"
    ]
    assert implement["custom_standards"] == ["不得改变公开 API"]
    assert detail["progress"]["method"] == "stage_completion"
    engine.store.close()


def test_custom_review_and_check_standards_are_hard_gates_with_runtime_evidence(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("verify custom delivery standards", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    revised = service.revise_contract(conversation_id, {
        "delivery_standard": {"custom_items": [
            {
                "id": "api-compatible",
                "stage_id": "implement",
                "text": "公开 API 保持向后兼容",
                "verifier": {"type": "review"},
            },
            {
                "id": "diff-clean",
                "stage_id": "test",
                "text": "差异通过冻结的完整性检查",
                "verifier": {"type": "check", "command_id": "diff-integrity"},
            },
        ]},
    })

    detail = service.start_work(conversation_id)
    candidate = detail["candidates"][-1]
    report = json.loads(Path(candidate["evidence_path"]).read_text(encoding="utf-8"))

    assert revised["version"] == 2
    assert candidate["status"] == "verified"
    assert detail["active_delivery"]["can_accept"] is True
    assert {item["status"] for item in detail["delivery_standard"]["custom_items"]} == {"passed"}
    assert {item["id"] for item in report["policy"]["requirements"]} >= {
        "standard.api-compatible", "standard.diff-clean"
    }
    check = next(item for item in report["records"] if item["kind"] == "check")
    review = next(item for item in report["records"] if item["kind"] == "review")
    assert "diff-clean" in check["criteria_ids"]
    assert review["standard_assessments"][0]["standard_id"] == "api-compatible"
    run = engine.store.get_run(candidate["run_id"])
    assert run["metadata"]["policy_snapshot"]["delivery_standard"] == revised["policy"]["delivery_standard"]
    engine.store.close()


@pytest.mark.parametrize("assessment_status", [None, "failed"])
def test_missing_or_failed_custom_review_assessment_blocks_exact_standard(
    workspace, monkeypatch, assessment_status
) -> None:
    class ReviewingProvider:
        def execute(self, stage_input):
            output = MockProvider().execute(stage_input)
            if stage_input.stage_id != "review":
                return output
            payload = json.loads(output.content)
            payload["standard_assessments"] = [] if assessment_status is None else [
                {
                    "standard_id": item["id"],
                    "status": assessment_status,
                    "note": "规则未满足",
                }
                for item in stage_input.context.get("review_standards", [])
            ]
            return replace(output, content=json.dumps(payload, ensure_ascii=False))

    monkeypatch.setattr(
        "muxdev.runtime.engine.get_runtime_provider",
        lambda *_args, **_kwargs: ReviewingProvider(),
    )
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("block incomplete custom review", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    service.revise_contract(conversation_id, {
        "delivery_standard": {"custom_items": [{
            "id": "review-required",
            "stage_id": "implement",
            "text": "Reviewer 必须逐项确认",
        }]},
    })

    detail = service.start_work(conversation_id)
    candidate = detail["candidates"][-1]
    report = json.loads(Path(candidate["evidence_path"]).read_text(encoding="utf-8"))
    evaluation = next(
        item for item in report["decision"]["requirements"]
        if item["requirement_id"] == "standard.review-required"
    )

    assert candidate["status"] == "failed"
    assert evaluation["status"] == ("missing" if assessment_status is None else "failed")
    assert any(
        item["requirement_id"] == "standard.review-required"
        for item in detail["active_delivery"]["blocking_items"]
    )
    engine.store.close()


def test_failed_bound_check_blocks_its_custom_standard(workspace, monkeypatch) -> None:
    def failed_check(_self, command, _worktree, _run_id, _stage_id):
        return ExecutedCheck(
            id=command.id,
            argv=command.argv,
            cwd=command.cwd,
            cwd_digest=canonical_hash("cwd"),
            exit_code=1,
            duration_ms=1,
            stdout_digest=canonical_hash(""),
            stderr_digest=canonical_hash("failed"),
            stderr_summary="failed",
        )

    monkeypatch.setattr(RunEngine, "_run_check", failed_check)
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("block failed custom check", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    service.revise_contract(conversation_id, {
        "delivery_standard": {"custom_items": [{
            "id": "bound-check",
            "stage_id": "test",
            "text": "冻结检查必须通过",
            "verifier": {"type": "check", "command_id": "diff-integrity"},
        }]},
    })

    detail = service.start_work(conversation_id)
    report = json.loads(Path(detail["candidates"][-1]["evidence_path"]).read_text(encoding="utf-8"))
    evaluation = next(
        item for item in report["decision"]["requirements"]
        if item["requirement_id"] == "standard.bound-check"
    )

    assert evaluation["status"] == "failed"
    assert "绑定的运行检查未通过" in evaluation["reason"]
    assert detail["active_delivery"]["can_accept"] is False
    engine.store.close()


def test_delivery_standard_revision_invalidates_candidate_and_rejects_baseline_edit(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("revise standard after verification", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    candidate = service.start_work(conversation_id)["candidates"][-1]

    revised = service.revise_contract(conversation_id, {
        "delivery_standard": {"custom_items": [{
            "id": "docs-current",
            "stage_id": "implement",
            "text": "受影响文档必须同步",
        }]},
    })
    detail = service.get(conversation_id)

    assert engine.store.get_delivery_candidate(candidate["candidate_id"])["status"] == "invalidated"
    assert revised["policy"]["revision"]["affected_stages"] == [
        "implement", "test", "review", "security_review", "fix"
    ]
    assert detail["active_delivery"]["status_label"] == "需要重新验证"
    assert revised["policy"]["revision"]["changed_fields"] == [
        "delivery_standard:implement"
    ]
    with pytest.raises(ValueError, match="built-in delivery standard fields cannot be edited"):
        service.revise_contract(conversation_id, {
            "delivery_standard": {"stages": []},
        })
    engine.store.close()


def test_standard_profile_custom_review_cannot_pass_without_independent_reviewer(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("require independent custom review", profile="standard")
    conversation_id = created["conversation"]["conversation_id"]
    service.revise_contract(conversation_id, {
        "delivery_standard": {"custom_items": [{
            "id": "independent-custom-review",
            "stage_id": "implement",
            "text": "自定义兼容性标准必须由独立 Reviewer 确认",
        }]},
    })

    detail = service.start_work(conversation_id)
    report = json.loads(Path(detail["candidates"][-1]["evidence_path"]).read_text(encoding="utf-8"))
    evaluation = next(
        item for item in report["decision"]["requirements"]
        if item["requirement_id"] == "standard.independent-custom-review"
    )

    blocker_ids = {
        item["requirement_id"] for item in report["decision"]["blockers"]
    }
    assert evaluation["status"] == "missing"
    assert detail["candidates"][-1]["status"] == "failed"
    assert blocker_ids >= {
        "independent_review", "standard.independent-custom-review"
    }
    engine.store.close()


def test_missing_delivery_summary_fails_closed_and_old_baseline_snapshot_is_stable(
    workspace, monkeypatch
) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("keep the frozen baseline", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    frozen = created["contracts"][-1]["policy"]["delivery_standard"]

    monkeypatch.setattr(
        "muxdev.runtime.delivery_standards._baseline_stages",
        lambda *_args: [{"stage_id": "future-baseline"}],
    )
    assert service.get(conversation_id)["delivery_standard"]["baseline_version"] == "sdlc-v2"
    assert service.get(conversation_id)["contracts"][-1]["policy"]["delivery_standard"] == frozen

    candidate = service.start_work(conversation_id)["candidates"][-1]
    Path(candidate["evidence_path"]).unlink()
    detail = service.get(conversation_id)

    assert detail["active_delivery"]["evidence_integrity"] == {
        "available": False, "valid": False
    }
    assert detail["active_delivery"]["can_accept"] is False
    engine.store.close()


def test_conversation_reserves_run_before_background_initialization(workspace, monkeypatch) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("persist the task before initialization", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    observed: list[dict] = []

    def fail_after_reservation(_workspace, run_id, _run_dir, **_kwargs):
        observed.append(engine.store.get_run(run_id) or {})
        raise RuntimeError("initialization interrupted")

    monkeypatch.setattr(
        "muxdev.runtime.engine.prepare_run_subject", fail_after_reservation
    )

    with pytest.raises(RuntimeError, match="initialization interrupted"):
        service.start_work(conversation_id)

    detail = service.get(conversation_id)
    assert observed and observed[0]["status"] == "created"
    assert detail["conversation"]["status"] == "needs_user"
    assert len(detail["runs"]) == 1
    assert detail["runs"][0]["run_id"] == detail["conversation"]["active_run_id"]
    assert any(
        event["type"] == "run.event"
        and event["payload"].get("source_type") == "run.initializing"
        for event in engine.store.conversation_events(conversation_id)
    )
    engine.store.close()


def test_get_repairs_legacy_verifying_conversation_without_a_run(workspace) -> None:
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)
    created = service.create("recover an orphaned task", profile="lite")
    conversation_id = created["conversation"]["conversation_id"]
    engine.store.update_conversation(
        conversation_id, status="verifying", active_run_id="run_missing"
    )

    repaired = service.get(conversation_id)

    assert repaired["conversation"]["status"] == "needs_user"
    assert repaired["recovery"]["primary_failure"]["code"] == "orphaned_execution"
    assert [item["action"] for item in repaired["recovery"]["next_actions"]] == [
        "start_work"
    ]
    assert sum(
        event["type"] == "run.orphaned"
        for event in engine.store.conversation_events(conversation_id)
    ) == 1

    restarted = service.action(conversation_id, "start_work")
    assert restarted["conversation"]["status"] == "awaiting_acceptance"
    assert len(restarted["runs"]) == 1
    engine.store.close()


def test_fallback_conversation_worktree_excludes_dependency_caches(workspace) -> None:
    (workspace / "src").mkdir()
    (workspace / "src" / "main.ts").write_text("export {};\n", encoding="utf-8")
    (workspace / "node_modules" / "large-package").mkdir(parents=True)
    (workspace / "node_modules" / "large-package" / "index.js").write_text(
        "generated dependency", encoding="utf-8"
    )
    engine = RunEngine(workspace)
    service = ConversationService(engine, engine.store)

    created = service.create("prepare a lean isolated worktree", profile="lite")
    worktree = Path(created["conversation"]["metadata"]["worktree"])

    assert (worktree / "src" / "main.ts").is_file()
    assert not (worktree / "node_modules").exists()
    assert any(
        event["type"] == "workspace.prepared"
        for event in engine.store.conversation_events(
            created["conversation"]["conversation_id"]
        )
    )
    engine.store.close()
