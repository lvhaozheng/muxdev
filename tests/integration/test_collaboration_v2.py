from __future__ import annotations

import time
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from muxdev.api import create_app
from muxdev.runtime import agent_sessions
from muxdev.runtime.agent_sessions import agent_session_manager
from muxdev.runtime import ConversationService, RunEngine
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
        assert len(reopened.table_names()) == 31
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
