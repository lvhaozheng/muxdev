from __future__ import annotations

import time
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from muxdev.api import create_app
from muxdev.runtime.agent_sessions import agent_session_manager
from muxdev.runtime import ConversationService, RunEngine
from muxdev.runtime.collaboration_service import CollaborationService
from muxdev.storage import ControlStore
from muxdev.services.evidence_verify import verify_evidence_report


def test_v2_direct_conversation_starts_isolated_cli_session(workspace):
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    client = TestClient(create_app(workspace))

    response = client.post(
        "/api/v2/conversations",
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
        f"/api/v2/sessions/{session_id}/terminal",
        headers={"origin": "http://testserver"},
    ) as websocket:
        websocket.send_json(
            {"type": "attach", "after_seq": 0, "cols": 100, "rows": 28, "request_write": True}
        )
        frames = [websocket.receive_json() for _ in range(3)]
        assert any(frame["type"] == "lease" and frame["granted"] for frame in frames)

    manager.close(session_id)
    with ControlStore(workspace) as store:
        assert store.get_conversation(detail["conversation"]["conversation_id"])["mode"] == "direct"


def test_v9_database_is_upgraded_to_v10_without_rewriting_conversations(workspace):
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
        assert len(reopened.table_names()) == 22


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
