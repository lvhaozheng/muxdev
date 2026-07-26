from __future__ import annotations

import json
import time
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from muxdev.api import create_app
from muxdev.runtime import agent_sessions
from muxdev.runtime.agent_sessions import (
    AgentSessionManager,
    SessionLifecycleError,
    agent_session_manager,
)
from muxdev.runtime import ConversationService, RunEngine
from muxdev.runtime.agent_session_state import (
    project_agent_turn_completed,
    project_runtime_resumed,
    project_runtime_waiting,
)
from muxdev.runtime.collaboration_service import CollaborationService
from muxdev.runtime.terminal import PipeTerminalBackend
from muxdev.storage import ControlStore
from muxdev.services import agents as agent_services
from muxdev.services.agents import AgentRegistry
from muxdev.services.evidence_verify import verify_evidence_report


def _project_api(client: TestClient) -> str:
    project_id = client.get("/api/v2/projects").json()[0]["project_id"]
    return f"/api/v2/projects/{project_id}"


def test_v2_direct_conversation_starts_isolated_cli_session(workspace):
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    response = client.post(
        f"{api}/conversations",
        json={"goal": "Inspect the fixture", "mode": "direct", "agent_id": "mock"},
    )
    assert response.status_code == 201, response.text
    detail = response.json()
    assert detail["mode"] == "direct"
    assert detail["assignments"][0]["status"] == "running"
    session_id = detail["sessions"][0]["session_id"]

    manager = agent_session_manager(workspace)
    deadline = time.monotonic() + 5
    events = []
    while time.monotonic() < deadline:
        events = manager.events(session_id)
        if any(item["direction"] == "output" for item in events):
            break
        time.sleep(0.05)
    assert any(item["direction"] == "output" for item in events)

    with client.websocket_connect(
        f"{api}/sessions/{session_id}/terminal",
        headers={"origin": "http://testserver"},
    ) as websocket:
        websocket.send_json(
            {"type": "attach", "after_seq": 0, "cols": 100, "rows": 28, "request_write": True}
        )
        frames = [websocket.receive_json() for _ in range(3)]
        assert any(frame["type"] == "lease" and frame["granted"] for frame in frames)
        websocket.send_json({"type": "heartbeat"})
        renewed = []
        for _ in range(5):
            frame = websocket.receive_json()
            renewed.append(frame)
            if frame["type"] == "lease" and frame.get("granted"):
                break
        assert any(
            frame["type"] == "lease" and frame.get("granted")
            for frame in renewed
        )

    manager.close(session_id)
    with ControlStore(workspace) as store:
        assert store.get_conversation(detail["conversation"]["conversation_id"])["mode"] == "direct"


def test_unavailable_agent_create_fails_without_leaving_a_conversation(
    workspace, monkeypatch
):
    original = AgentRegistry._resolve_executable

    def resolve(command: str) -> str | None:
        if command == "claude":
            return None
        return original(command)

    monkeypatch.setattr(AgentRegistry, "_resolve_executable", staticmethod(resolve))
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    response = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "mode": "direct",
            "agent_id": "claude-code",
            "deliverables": [{"type": "answer"}],
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "executable_not_found"
    assert "当前不可用" in detail["message"]
    assert detail["retryable"] is False
    with ControlStore(workspace) as store:
        assert store.list_conversations() == []


def test_pty_required_agent_is_unavailable_on_pipe_without_creating_state(
    workspace,
    monkeypatch,
):
    monkeypatch.setattr(
        AgentRegistry,
        "_resolve_executable",
        staticmethod(lambda command: f"C:/tools/{command}.exe"),
    )
    monkeypatch.setattr(
        agent_services,
        "_terminal_capabilities",
        lambda **_kwargs: {
            "backend": "pipe",
            "pty": False,
            "resize": False,
            "process_resume": False,
            "degraded": True,
            "degradation_reason": "PTY unavailable",
        },
    )
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    agents = {
        item["agent_id"]: item
        for item in client.get(f"{api}/agents").json()
    }
    assert agents["codex"]["available"] is False
    assert agents["codex"]["availability_code"] == "pty_unavailable"
    response = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "mode": "direct",
            "agent_id": "codex",
            "deliverables": [{"type": "answer"}],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "pty_unavailable"
    with ControlStore(workspace) as store:
        assert store.list_conversations() == []


def test_spawn_failure_returns_created_recoverable_conversation(
    workspace,
    monkeypatch,
):
    def fail_spawn(*_args, **_kwargs) -> None:
        raise OSError("simulated spawn failure")

    failed_backend = PipeTerminalBackend()
    monkeypatch.setattr(failed_backend, "spawn", fail_spawn)
    monkeypatch.setattr(
        agent_sessions,
        "terminal_backend",
        lambda **_kwargs: failed_backend,
    )
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    response = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "mode": "direct",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
        },
    )

    assert response.status_code == 201, response.text
    detail = response.json()
    assert detail["conversation"]["status"] == "needs_user"
    assert detail["conversation"]["metadata"]["startup_error"]["code"] == (
        "session_launch_failed"
    )
    assert detail["assignments"][0]["status"] == "failed"
    assert detail["runs"][0]["status"] == "failed"
    assert detail["runs"][0]["run_id"] == detail["conversation"]["active_run_id"]
    assert detail["sessions"][0]["status"] == "failed"
    assert any(
        item["type"] == "run.failed_to_start"
        for item in detail["timeline"]
    )


def test_cli_startup_timeout_does_not_leave_assignment_running(
    workspace,
    monkeypatch,
):
    def fail_readiness(
        _manager,
        live,
        *,
        timeout_seconds,
    ) -> None:
        raise SessionLifecycleError(
            "CLI never became ready",
            code="session_startup_timeout",
            remediation="inspect startup UI",
            retryable=True,
            session_id=live.session_id,
        )

    monkeypatch.setattr(
        AgentSessionManager,
        "_wait_until_ready",
        fail_readiness,
    )
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    response = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "mode": "direct",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
        },
    )

    assert response.status_code == 201, response.text
    detail = response.json()
    assert detail["conversation"]["status"] == "needs_user"
    assert detail["conversation"]["metadata"]["startup_error"]["code"] == (
        "session_startup_timeout"
    )
    assert detail["assignments"][0]["status"] == "failed"
    assert detail["runs"][0]["status"] == "failed"
    assert detail["sessions"][0]["status"] == "failed"
    failed_event = next(
        item
        for item in detail["timeline"]
        if item["type"] == "run.failed_to_start"
    )
    assert failed_event["payload"]["error"]["code"] == "session_startup_timeout"


def test_cli_runtime_blocker_moves_assignment_to_waiting_user(workspace):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    created = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "mode": "direct",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
        },
    ).json()
    session = created["sessions"][0]
    manager = agent_session_manager(workspace)

    assert project_runtime_waiting(workspace, session["session_id"]) is True

    detail = client.get(
        f"{api}/conversations/{created['conversation']['conversation_id']}"
    ).json()
    assert detail["conversation"]["status"] == "needs_user"
    assert detail["assignments"][0]["status"] == "waiting_user"
    assert detail["runs"][0]["status"] == "waiting_user"
    assert detail["sessions"][0]["status"] == "waiting_input"
    assert (
        detail["assignments"][0]["metadata"]["waiting_reason"]["code"]
        == "agent_cli_usage_limit"
    )
    assert any(
        item["type"] == "assignment.waiting_user"
        for item in detail["timeline"]
    )
    snapshot = client.get(
        f"{api}/conversations/{created['conversation']['conversation_id']}/snapshot"
    ).json()
    assert snapshot["attention_detail"]["kind"] == "cli_input"
    assert snapshot["attention_detail"]["action"] == "open_terminal"
    assert "额度" in snapshot["attention_detail"]["message"]
    assert snapshot["next_actions"][0] == "open_terminal"

    assert project_runtime_resumed(
        workspace,
        session["session_id"],
        trigger="integration_test",
    )
    resumed = client.get(
        f"{api}/conversations/{created['conversation']['conversation_id']}"
    ).json()
    assert resumed["conversation"]["status"] == "working"
    assert resumed["assignments"][0]["status"] == "running"
    assert resumed["runs"][0]["status"] == "running"
    assert resumed["sessions"][0]["status"] == "busy"
    resumed_snapshot = client.get(
        f"{api}/conversations/{created['conversation']['conversation_id']}/snapshot"
    ).json()
    assert resumed_snapshot["attention_detail"] is None
    assert any(
        item["type"] == "assignment.resumed"
        for item in resumed["timeline"]
    )
    manager.close(session["session_id"])


def test_observed_cli_completion_auto_reports_running_assignment(workspace):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    created = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "mode": "direct",
            "agent_id": "mock",
            "profile": "lite",
            "deliverables": [{"type": "answer"}],
        },
    ).json()
    conversation_id = created["conversation"]["conversation_id"]
    assignment = created["assignments"][0]
    session = created["sessions"][0]

    assert project_agent_turn_completed(
        workspace,
        session["session_id"],
        "Inspection finished with a concrete answer.\nstatus: completed · tokens: 42",
    )

    with ControlStore(workspace) as store:
        completed = store.get_assignment(assignment["assignment_id"])
        updated_session = store.get_agent_session(session["session_id"])
        assert completed and completed["status"] == "completed"
        assert completed["metadata"]["report"]["capture_grade"] == "observed"
        assert (
            updated_session
            and updated_session["metadata"]["auto_reported_generation"]
            == session["generation"]
        )
        events = store.conversation_events(conversation_id)
        assert any(item["type"] == "assignment.auto_reported" for item in events)

    manager = agent_session_manager(workspace)
    with ControlStore(workspace) as store:
        sessions = store.list_agent_sessions(conversation_id)
    for item in sessions:
        if str(item.get("status") or "") not in {"closed", "failed"}:
            manager.close(str(item["session_id"]))


def test_daemon_reconciles_completed_turn_from_existing_transcript(workspace):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    created = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "mode": "direct",
            "agent_id": "mock",
            "profile": "lite",
            "deliverables": [{"type": "answer"}],
        },
    ).json()
    conversation_id = created["conversation"]["conversation_id"]
    assignment = created["assignments"][0]
    session = created["sessions"][0]
    session_id = session["session_id"]
    manager = agent_session_manager(workspace)
    manager.registry.adapters["mock"] = manager.registry.adapters[
        "mock"
    ].model_copy(
        update={
            "turn_completed_pattern": (
                r"(?im)^status:\s*completed"
                r"(?:\s*[·•]\s*tokens:\s*\d+)?\s*$"
            )
        }
    )
    with manager._lock:
        live = manager._sessions.pop(session_id)
    live.closing = True
    live.backend.close()
    if live.reader:
        live.reader.join(timeout=2)
    events = manager.events(session_id)
    sequence = max((int(item["seq"]) for item in events), default=0) + 1
    transcript = Path(str(session["transcript_path"]))
    with transcript.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(
                {
                    "seq": sequence,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "direction": "output",
                    "data": "Existing final answer\nstatus: completed · tokens: 42\n",
                    "metadata": {},
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    with ControlStore(workspace) as store:
        store.update_agent_session(
            session_id,
            status="ready",
            last_sequence=sequence,
        )

    assert manager.reconcile_orphans() == 1

    with ControlStore(workspace) as store:
        completed = store.get_assignment(assignment["assignment_id"])
        assert completed and completed["status"] == "completed"
        assert any(
            item["type"] == "assignment.auto_reported"
            for item in store.conversation_events(conversation_id)
        )
        sessions = store.list_agent_sessions(conversation_id)
    for item in sessions:
        if str(item.get("status") or "") not in {"closed", "failed"}:
            try:
                manager.close(str(item["session_id"]))
            except RuntimeError:
                pass


def test_waiting_assignment_message_queues_then_resumes_on_stable_generation(
    workspace,
):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    created = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "mode": "direct",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
        },
    ).json()
    conversation_id = created["conversation"]["conversation_id"]
    assignment = created["assignments"][0]
    run = created["runs"][0]
    session = created["sessions"][0]
    session_id = session["session_id"]
    manager = agent_session_manager(workspace)

    assert project_runtime_waiting(workspace, session_id) is True
    queued = client.post(
        f"{api}/conversations/{conversation_id}/messages",
        json={"content": "Continue after the CLI is ready"},
    )
    assert queued.status_code == 202, queued.text
    queued_body = queued.json()
    assert queued_body["delivery_status"] == "queued"
    assert queued_body["recipients"][0]["status"] == "queued"
    assert queued_body["recipients"][0]["assignment_id"] == assignment["assignment_id"]
    with ControlStore(workspace) as store:
        deliveries = store.list_message_deliveries(
            conversation_id=conversation_id
        )
        assert len(deliveries) == 1
        assert deliveries[0]["status"] == "pending"
        assert store.get_assignment(assignment["assignment_id"])["status"] == "waiting_user"
        assert store.get_run(run["run_id"])["status"] == "waiting_user"
        assert store.get_conversation(conversation_id)["status"] == "needs_user"

    lease = manager.acquire_write_lease(session_id, holder="integration-test")
    manager.write(
        session_id,
        "/usage\n",
        holder="integration-test",
        lease_id=lease["lease_id"],
    )
    time.sleep(0.8)

    engine = RunEngine(workspace)
    service = CollaborationService(
        ConversationService(engine, engine.store),
        engine.store,
    )
    try:
        assert service.dispatch_pending_messages(force=True) == 1
    finally:
        engine.store.close()

    with ControlStore(workspace) as store:
        delivery = store.list_message_deliveries(
            conversation_id=conversation_id
        )[0]
        assert delivery["status"] == "dispatched"
        assert delivery["target_generation"] == session["generation"]
        assert store.get_assignment(assignment["assignment_id"])["status"] == "running"
        assert store.get_run(run["run_id"])["status"] == "running"
        assert store.get_conversation(conversation_id)["status"] == "working"
        events = store.conversation_events(conversation_id)
        assert any(item["type"] == "message.queued" for item in events)
        dispatched = next(
            item for item in events if item["type"] == "message.dispatched"
        )
        assert dispatched["generation"] == session["generation"]
        assert dispatched["payload"]["resumed_assignment"] is True
    assert any(
        item.get("metadata", {}).get("delivery_id") == delivery["delivery_id"]
        for item in manager.events(session_id)
        if item["direction"] == "input"
    )
    manager.close(session_id)


def test_deliverable_and_required_rule_are_composed_without_partial_create(
    workspace,
):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    rule = {
        "rule_id": "required.delivery-review",
        "version": 1,
        "title": "Required delivery review",
        "description": "Require a structured delivery review.",
        "kind": "delivery_standard",
        "workflows": ["change"],
        "instructions": "Review the requested deliverable against the frozen contract.",
        "enforcement": "required",
        "delivery_items": [
            {
                "stage_id": "review",
                "deliverable": "Rule compliance review",
                "completion": "The requested deliverable satisfies the Rule.",
                "proof": "Structured Agent review evidence",
                "verifier": {"type": "agent_review"},
            }
        ],
    }
    assert client.post("/api/v2/rules", json=rule).status_code == 201

    response = client.post(
        f"{api}/conversations",
        json={
            "goal": "Explain README.md",
            "mode": "direct",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
            "rule_ids": [rule["rule_id"]],
            "auto_start_when_ready": False,
        },
    )

    assert response.status_code == 201, response.text
    conversation_id = response.json()["conversation"]["conversation_id"]
    detail = client.get(f"{api}/conversations/{conversation_id}").json()
    item_ids = {
        item["id"] for item in detail["delivery_standard"]["custom_items"]
    }
    assert "deliverable.answer.1" in item_ids
    assert any(item_id.startswith("rule.") for item_id in item_ids)
    with ControlStore(workspace) as store:
        assert len(store.list_conversations()) == 1


def test_invalid_delivery_standard_is_rejected_before_conversation_is_created(
    workspace,
):
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    response = client.post(
        f"{api}/conversations",
        json={
            "goal": "Explain README.md",
            "mode": "direct",
            "agent_id": "mock",
            "delivery_standard": {
                "custom_items": [
                    {
                        "stage_id": "review",
                        "deliverable": "Explanation",
                        "completion": "Explanation is complete",
                    }
                ]
            },
            "auto_start_when_ready": False,
        },
    )

    assert response.status_code == 422
    with ControlStore(workspace) as store:
        assert store.list_conversations() == []


def test_v9_database_is_upgraded_to_v11_without_rewriting_conversations(workspace):
    root = workspace / ".muxdev"
    root.mkdir(parents=True)
    connection = sqlite3.connect(root / "control.sqlite")
    connection.execute(
        """CREATE TABLE conversations(
          conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL,
          status TEXT NOT NULL, workspace TEXT NOT NULL, active_contract_id TEXT,
          active_run_id TEXT, active_candidate_id TEXT, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, metadata TEXT NOT NULL
        )"""
    )
    connection.execute(
        "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)",
        ("conv_legacy", "legacy", "legacy", "working", str(workspace), "now", "now", "{}"),
    )
    connection.commit()
    connection.close()
    with ControlStore(workspace) as reopened:
        assert reopened.get_conversation("conv_legacy")["mode"] == "legacy_pipeline"
        assert len(reopened.table_names()) == 35
        assert "conversation_interactions" in reopened.table_names()
        assert "session_generations" in reopened.table_names()


def test_v11_database_is_upgraded_to_v12_and_delivered_becomes_idle(workspace):
    with ControlStore(workspace) as store:
        store.create_conversation(
            conversation_id="conv_v11",
            title="v11",
            goal="migrate",
            status="delivered",
            metadata={},
            mode="direct",
        )
        store.create_run(
            run_id="run_v11",
            run_kind="assignment",
            conversation_id="conv_v11",
            task="migrate",
            workflow="change",
            profile="standard",
            provider="mock",
            policy_hash="sha256:v11",
        )
    connection = sqlite3.connect(workspace / ".muxdev" / "control.sqlite")
    connection.execute("DROP INDEX IF EXISTS ix_conversation_events_correlation")
    for column in ("turn_index", "review_state", "reviewed_at"):
        connection.execute(f"ALTER TABLE runs DROP COLUMN {column}")
    for column in (
        "schema_version",
        "actor_kind",
        "actor_id",
        "assignment_id",
        "session_id",
        "generation",
        "correlation_id",
        "capture_grade",
    ):
        connection.execute(f"ALTER TABLE conversation_events DROP COLUMN {column}")
    for table in ("file_baselines", "file_changes", "verification_attempts"):
        connection.execute(f"DROP TABLE {table}")
    connection.execute("DELETE FROM schema_migrations WHERE version = 12")
    connection.commit()
    connection.close()

    with ControlStore(workspace) as reopened:
        conversation = reopened.get_conversation("conv_v11")
        run = reopened.get_run("run_v11")
        run_columns = {
            str(row[1])
            for row in reopened.connection.execute("PRAGMA table_info(runs)")
        }
        event_columns = {
            str(row[1])
            for row in reopened.connection.execute(
                "PRAGMA table_info(conversation_events)"
            )
        }

    assert conversation["status"] == "idle"
    assert run["turn_index"] == 1
    assert run["review_state"] == "pending"
    assert {"turn_index", "review_state", "reviewed_at"} <= run_columns
    assert {"schema_version", "capture_grade", "correlation_id"} <= event_columns


def test_ambiguous_conversation_clarifies_without_assignment_or_run(workspace):
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    response = client.post(
        f"{api}/conversations",
        json={"goal": "帮我优化一下", "mode": "direct", "agent_id": "mock"},
    )

    assert response.status_code == 201, response.text
    detail = response.json()
    assert detail["conversation"]["status"] == "clarifying"
    assert detail["requirements"]["status"] == "needs_input"
    assert detail["assignments"] == []
    assert detail["runs"] == []
    assert len(detail["interactions"]) == 1
    assert detail["interactions"][0]["run_id"] is None
    assert detail["sessions"] == []
    with ControlStore(workspace) as store:
        reviews = store.list_review_records(
            detail["conversation"]["conversation_id"]
        )
    assert reviews[0]["kind"] == "interaction"
    assert reviews[0]["status"] == "pending"

    interaction_id = detail["interactions"][0]["interaction_id"]
    answered = client.post(
        f"{api}/conversations/{detail['conversation']['conversation_id']}/interactions/{interaction_id}/respond",
        json={"response": "修改代码并补充测试"},
    )
    assert answered.status_code == 200, answered.text
    resumed = answered.json()["conversation"]
    assert len(resumed["assignments"]) == 1
    assert len([item for item in resumed["runs"] if item["run_kind"] == "assignment"]) == 1
    assert resumed["assignments"][0]["run_id"] == resumed["runs"][0]["run_id"]
    with ControlStore(workspace) as store:
        reviews = store.list_review_records(
            detail["conversation"]["conversation_id"]
        )
    assert reviews[0]["status"] == "responded"
    agent_session_manager(workspace).close(resumed["sessions"][0]["session_id"])


def test_messages_join_agents_without_runs_but_explicit_consult_creates_one(workspace):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    create_response = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "agent_id": "mock",
            "auto_start_when_ready": False,
            "deliverables": [{"type": "answer"}],
        },
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    conversation_id = created["conversation"]["conversation_id"]
    assert created["assignments"] == [] and created["runs"] == []

    joined = client.post(
        f"{api}/conversations/{conversation_id}/messages",
        json={"content": "@mock-review 请加入会话", "dispatch_kind": "message"},
    )
    assert joined.status_code == 202, joined.text
    detail = client.get(f"{api}/conversations/{conversation_id}").json()
    assert detail["assignments"] == [] and detail["runs"] == []
    assert {item["agent_id"] for item in detail["sessions"]} == {"mock-review"}

    consult = client.post(
        f"{api}/conversations/{conversation_id}/messages",
        json={"content": "请评审目标", "recipients": ["mock-review"], "dispatch_kind": "consult"},
    )
    assert consult.status_code == 202, consult.text
    detail = client.get(f"{api}/conversations/{conversation_id}").json()
    assert len(detail["assignments"]) == 1
    assert len(detail["runs"]) == 1
    assert detail["runs"][0]["run_kind"] == "assignment"
    for session in detail["sessions"]:
        agent_session_manager(workspace).close(session["session_id"])


def test_idle_message_starts_next_turn_and_reuses_logical_session(workspace):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    created = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
        },
    ).json()
    conversation_id = created["conversation"]["conversation_id"]
    first_assignment = created["assignments"][0]
    first_run = created["runs"][0]
    session_id = created["sessions"][0]["session_id"]
    with ControlStore(workspace) as store:
        store.update_assignment(first_assignment["assignment_id"], status="completed")
        store.update_run(first_run["run_id"], status="completed", current_stage=None)
        store.settle_run_review(first_run["run_id"], "answered")
        store.update_agent_session(session_id, current_assignment_id=None)
        store.update_conversation(conversation_id, status="idle")

    response = client.post(
        f"{api}/conversations/{conversation_id}/messages",
        json={"content": "Now inspect pyproject.toml"},
    )
    assert response.status_code == 202, response.text
    with ControlStore(workspace) as store:
        runs = store.list_conversation_runs(conversation_id)
        sessions = store.list_agent_sessions(conversation_id)
        conversation = store.get_conversation(conversation_id) or {}

    assert [item["turn_index"] for item in runs] == [1, 2]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == session_id
    assert conversation["active_run_id"] == runs[-1]["run_id"]
    agent_session_manager(workspace).close(session_id)


def test_failed_session_requires_restart_without_recording_a_ghost_message(
    workspace,
):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    created = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
        },
    ).json()
    conversation_id = created["conversation"]["conversation_id"]
    assignment = created["assignments"][0]
    run = created["runs"][0]
    session = created["sessions"][0]
    manager = agent_session_manager(workspace)
    manager.close(session["session_id"])
    with ControlStore(workspace) as store:
        store.update_agent_session(
            session["session_id"],
            status="failed",
            current_assignment_id=assignment["assignment_id"],
            metadata={"launch_error": "simulated failure"},
        )
        store.update_assignment(assignment["assignment_id"], status="failed")
        store.update_run(run["run_id"], status="failed", current_stage=None)
        store.update_conversation(
            conversation_id,
            status="needs_user",
            active_run_id=run["run_id"],
        )
        before = sum(
            1
            for item in store.conversation_events(conversation_id)
            if item["type"] == "user.message"
        )

    rejected = client.post(
        f"{api}/conversations/{conversation_id}/messages",
        json={"content": "Continue without losing this draft"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "session_restart_required"
    with client.websocket_connect(
        f"{api}/sessions/{session['session_id']}/terminal",
        headers={"origin": "http://testserver"},
    ) as websocket:
        websocket.send_json(
            {
                "type": "attach",
                "after_seq": 0,
                "request_write": True,
                "cols": 100,
                "rows": 28,
            }
        )
        frames = [websocket.receive_json() for _ in range(3)]
        assert any(
            frame["type"] == "lease" and not frame["granted"]
            for frame in frames
        )
        status = next(frame for frame in frames if frame["type"] == "status")
        assert status["session"]["attached"] is False
        assert status["session"]["can_restart"] is True
        assert status["session"]["failure"]["message"] == "simulated failure"
    with ControlStore(workspace) as store:
        after = sum(
            1
            for item in store.conversation_events(conversation_id)
            if item["type"] == "user.message"
        )
    assert after == before

    restarted = client.post(
        f"{api}/sessions/{session['session_id']}/restart"
    )
    assert restarted.status_code == 200, restarted.text
    assert restarted.json()["session"]["generation"] == session["generation"] + 1
    sent = client.post(
        f"{api}/conversations/{conversation_id}/messages",
        json={"content": "Continue without losing this draft"},
    )
    assert sent.status_code == 202, sent.text
    with ControlStore(workspace) as store:
        assert len(store.list_conversation_runs(conversation_id)) == 1
    manager.close(session["session_id"])


def test_daemon_reconciliation_repairs_failed_session_execution_once(workspace):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    created = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
        },
    ).json()
    conversation_id = created["conversation"]["conversation_id"]
    assignment = created["assignments"][0]
    run = created["runs"][0]
    session = created["sessions"][0]
    manager = agent_session_manager(workspace)
    manager.close(session["session_id"])
    with ControlStore(workspace) as store:
        store.update_agent_session(
            session["session_id"],
            status="failed",
            current_assignment_id=assignment["assignment_id"],
            metadata={"launch_error": "legacy immediate exit"},
        )
        store.update_assignment(assignment["assignment_id"], status="running")
        store.update_run(run["run_id"], status="created")
        store.update_conversation(
            conversation_id,
            status="working",
        )

    assert manager.reconcile_execution_state() == 1
    assert manager.reconcile_execution_state() == 0
    with ControlStore(workspace) as store:
        assert store.get_assignment(assignment["assignment_id"])["status"] == "failed"
        assert store.get_run(run["run_id"])["status"] == "failed"
        conversation = store.get_conversation(conversation_id) or {}
        assert conversation["status"] == "needs_user"
        assert conversation["active_run_id"] == run["run_id"]


def test_concurrent_resumes_create_only_one_generation(workspace):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    created = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
        },
    ).json()
    assignment = created["assignments"][0]
    session = created["sessions"][0]
    manager = agent_session_manager(workspace)
    manager.close(session["session_id"])
    with ControlStore(workspace) as store:
        store.update_agent_session(
            session["session_id"],
            status="resumable",
            current_assignment_id=assignment["assignment_id"],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: manager.ensure_live(session["session_id"]),
                range(2),
            )
        )
    assert {item["session_id"] for item in results} == {session["session_id"]}
    with ControlStore(workspace) as store:
        generations = store.list_session_generations(session["session_id"])
    assert [item["generation"] for item in generations] == [1, 2]
    manager.close(session["session_id"])


def test_retry_keeps_assignment_run_and_logical_session_but_adds_generation(workspace):
    engine = RunEngine(workspace)
    service = CollaborationService(ConversationService(engine, engine.store), engine.store)
    try:
        detail = service.create(
            "Inspect README.md",
            agent_id="mock",
            deliverables=[{"type": "answer"}],
            auto_start_when_ready=False,
        )
        conversation_id = detail["conversation"]["conversation_id"]
        assignment = service.dispatch(
            conversation_id,
            {
                "agent_id": "mock",
                "dispatch_kind": "consult",
                "work_mode": "consult",
                "title": "Inspect",
                "brief": "Inspect README.md",
                "deliverables": ["answer"],
                "completion": ["answer supplied"],
                "proof": ["report manifest"],
            },
        )
        run_id = assignment["run_id"]
        before = service.store.find_agent_session(conversation_id, "mock", "main")

        retried = service.retry(assignment["assignment_id"])
        after = service.store.find_agent_session(conversation_id, "mock", "main")

        assert retried["run_id"] == run_id
        assert after["session_id"] == before["session_id"]
        assert after["generation"] == before["generation"] + 1
        assert [item["generation"] for item in service.store.list_session_generations(after["session_id"])] == [1, 2]
        assert service.store.stages(run_id)[0]["attempt"] == 2
    finally:
        for session in service.store.list_agent_sessions(conversation_id):
            if session["status"] not in {"closed", "failed"}:
                agent_session_manager(workspace).close(session["session_id"])
        engine.store.close()


def test_same_agent_assignments_share_one_conversation_terminal(workspace):
    engine = RunEngine(workspace)
    service = CollaborationService(
        ConversationService(engine, engine.store), engine.store
    )
    conversation_id = ""
    try:
        detail = service.create(
            "Inspect two areas in sequence",
            agent_id="mock",
            deliverables=[{"type": "answer"}],
            auto_start_when_ready=False,
        )
        conversation_id = detail["conversation"]["conversation_id"]
        common = {
            "agent_id": "mock",
            "dispatch_kind": "manual",
            "work_mode": "consult",
            "deliverables": ["answer"],
            "completion": ["answer supplied"],
            "proof": ["report manifest"],
        }
        first = service.dispatch(
            conversation_id,
            {
                **common,
                "title": "First inspection",
                "brief": "Inspect the first area",
            },
        )
        before = service.store.find_conversation_agent_session(
            conversation_id, "mock"
        )
        second = service.dispatch(
            conversation_id,
            {
                **common,
                "title": "Second inspection",
                "brief": "Inspect the second area",
            },
        )

        assert before
        assert second["status"] == "queued"
        assert len(service.store.list_agent_sessions(conversation_id)) == 1
        assert (
            service.store.find_conversation_agent_session(
                conversation_id, "mock"
            )["current_assignment_id"]
            == first["assignment_id"]
        )

        service.store.update_assignment(first["assignment_id"], status="completed")
        service.store.update_agent_session(
            before["session_id"], current_assignment_id=None, status="ready"
        )
        assert service.schedule_queued() == 1

        after = service.store.find_conversation_agent_session(
            conversation_id, "mock"
        )
        assert after
        assert after["session_id"] == before["session_id"]
        assert after["generation"] == before["generation"] + 1
        assert after["current_assignment_id"] == second["assignment_id"]
        assert len(service.store.list_agent_sessions(conversation_id)) == 1
    finally:
        if conversation_id:
            for session in service.store.list_agent_sessions(conversation_id):
                if session["status"] not in {"closed", "failed"}:
                    agent_session_manager(workspace).close(session["session_id"])
        engine.store.close()


def test_schema_converges_existing_duplicate_agent_terminals(workspace):
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    detail = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
        },
    ).json()
    conversation_id = detail["conversation"]["conversation_id"]
    original = detail["sessions"][0]
    manager = agent_session_manager(workspace)
    manager.close(original["session_id"])

    duplicate_id = "ses_duplicate_legacy_lane"
    with ControlStore(workspace) as store:
        store.connection.execute(
            "DROP INDEX uq_agent_sessions_conversation_agent_current"
        )
        row = store.get_agent_session(original["session_id"]) or {}
        columns = [
            str(item[1])
            for item in store.connection.execute(
                "PRAGMA table_info(agent_sessions)"
            ).fetchall()
        ]
        duplicate = dict(row)
        duplicate.update(
            {
                "session_id": duplicate_id,
                "lane_key": "assignment:legacy",
                "lane_type": "temporary",
                "status": "ready",
                "metadata": json.dumps(
                    {"legacy_duplicate_fixture": True},
                    ensure_ascii=False,
                ),
                "superseded_by_session_id": None,
            }
        )
        values = [
            (
                json.dumps(duplicate[column], ensure_ascii=False)
                if column == "metadata"
                and not isinstance(duplicate[column], str)
                else duplicate[column]
            )
            for column in columns
        ]
        store.connection.execute(
            f"""INSERT INTO agent_sessions({", ".join(columns)})
                VALUES ({", ".join("?" for _ in columns)})""",
            values,
        )
        store.connection.commit()

    with ControlStore(workspace) as migrated:
        sessions = migrated.list_agent_sessions(conversation_id)
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == duplicate_id
        assert sessions[0]["lane_key"] == "main"
        superseded = migrated.get_agent_session(original["session_id"])
        assert superseded
        assert superseded["status"] == "closed"
        assert superseded["superseded_by_session_id"] == duplicate_id


def test_orchestrated_plan_approves_once_and_merges_parallel_writes_stably(workspace):
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    engine = RunEngine(workspace)
    service = CollaborationService(ConversationService(engine, engine.store), engine.store)
    try:
        detail = service.create(
            "Create two independent artifacts",
            mode="orchestrated",
            agent_id="mock",
            orchestrator_agent_id="mock-orchestrator",
            max_parallel=4,
        )
        conversation_id = detail["conversation"]["conversation_id"]
        plan = service.propose_plan(
            conversation_id,
            {
                "summary": "two parallel files",
                "max_parallel": 4,
                "nodes": [
                    {
                        "id": "alpha",
                        "title": "Alpha",
                        "brief": "Create alpha.txt",
                        "agent_id": "mock",
                        "role": "implementer",
                        "dependencies": [],
                        "work_mode": "write",
                        "deliverables": ["alpha.txt"],
                        "completion": ["alpha.txt exists"],
                        "proof": ["changeset"],
                    },
                    {
                        "id": "beta",
                        "title": "Beta",
                        "brief": "Create beta.txt",
                        "agent_id": "mock-review",
                        "role": "implementer",
                        "dependencies": [],
                        "work_mode": "write",
                        "deliverables": ["beta.txt"],
                        "completion": ["beta.txt exists"],
                        "proof": ["changeset"],
                    },
                ],
            },
        )
        approved = service.approve_plan(conversation_id, plan["plan_id"])
        nodes = sorted(
            [item for item in approved["assignments"] if item["plan_id"] == plan["plan_id"]],
            key=lambda item: item["assignment_id"],
        )
        assert [item["status"] for item in nodes] == ["running", "running"]

        for index, assignment in enumerate(nodes):
            (Path(assignment["worktree"]) / f"artifact-{index}.txt").write_text(
                f"artifact {index}\n", encoding="utf-8"
            )
        high, low = nodes[1], nodes[0]
        manifest = {"summary": "done", "deliverables": ["file"], "proof": ["changeset"]}
        service.report(high["assignment_id"], manifest)
        assert service.store.get_assignment(high["assignment_id"])["status"] == "ready_to_merge"
        service.report(low["assignment_id"], manifest)
        assert service.store.get_assignment(low["assignment_id"])["status"] == "completed"
        assert service.store.get_assignment(high["assignment_id"])["status"] == "completed"
        merged = [
            item["payload"]["assignment_id"]
            for item in service.store.conversation_events(conversation_id)
            if item["type"] == "assignment.merged"
        ]
        assert merged[:2] == [low["assignment_id"], high["assignment_id"]]
    finally:
        manager = agent_session_manager(workspace)
        for session in service.store.list_agent_sessions(conversation_id):
            if session["status"] not in {"closed", "failed"}:
                manager.close(session["session_id"])
        engine.store.close()


def test_direct_delivery_runs_independent_review_evidence_and_accepts_safely(workspace):
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    engine = RunEngine(workspace)
    service = CollaborationService(ConversationService(engine, engine.store), engine.store)
    conversation_id = ""
    try:
        detail = service.create("Add delivery.txt", mode="direct", agent_id="mock")
        conversation_id = detail["conversation"]["conversation_id"]
        primary = detail["assignments"][0]
        (Path(primary["worktree"]) / "delivery.txt").write_text("trusted\n", encoding="utf-8")
        service.report(
            primary["assignment_id"],
            {"summary": "implemented", "deliverables": ["delivery.txt"], "proof": ["changeset"]},
        )
        reviewers = [
            item for item in service.store.list_assignments(conversation_id)
            if item["dispatch_kind"] == "review"
        ]
        assert len(reviewers) == 1
        result = service.report(
            reviewers[0]["assignment_id"],
            {
                "summary": "independent review passed",
                "status": "passed",
                "deliverables": ["review"],
                "proof": ["review manifest"],
                "findings": [],
            },
        )
        assert result["status"] == "completed"
        delivery = service.get(conversation_id)
        candidate = delivery["candidates"][-1]
        assert candidate["status"] == "verified"
        verification = verify_evidence_report(Path(candidate["evidence_path"]), store=service.store)
        assert verification["valid"] is True
        service.conversations.accept_delivery(conversation_id, candidate["candidate_id"])
        assert (workspace / "delivery.txt").read_text(encoding="utf-8") == "trusted\n"
    finally:
        manager = agent_session_manager(workspace)
        if conversation_id:
            for session in service.store.list_agent_sessions(conversation_id):
                if session["status"] not in {"closed", "failed"}:
                    manager.close(session["session_id"])
        engine.store.close()
