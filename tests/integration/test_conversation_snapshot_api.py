from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from muxdev.api import create_app
from muxdev.api.conversation_experience import _stream_events
from muxdev.runtime import ConversationService, RunEngine
from muxdev.runtime.agent_sessions import agent_session_manager
from muxdev.runtime.collaboration_service import CollaborationService
from muxdev.storage import ControlStore


def _project_api(client: TestClient) -> str:
    project_id = client.get("/api/v2/projects").json()[0]["project_id"]
    return f"/api/v2/projects/{project_id}"


def _seed_conversation(workspace) -> None:
    (workspace / "hello.txt").write_text("hello changed\n", encoding="utf-8")
    with ControlStore(workspace) as store:
        store.create_conversation(
            conversation_id="conv_snapshot",
            title="Conversation API",
            goal="exercise Conversation",
            status="awaiting_acceptance",
            metadata={"worktree": str(workspace)},
            mode="direct",
            primary_agent_id="mock",
        )
        store.create_run(
            run_id="run_snapshot",
            run_kind="delivery_verification",
            conversation_id="conv_snapshot",
            task="exercise Conversation",
            workflow="change",
            profile="standard",
            provider="mock",
            policy_hash="sha256:test",
            metadata={"worktree": str(workspace)},
        )
        store.update_conversation("conv_snapshot", active_run_id="run_snapshot")
        store.append_conversation_event(
            "conv_snapshot",
            "workspace.reconciled",
            {"file_count": 1},
            actor="runtime",
            run_id="run_snapshot",
            capture_grade="verified",
        )
        store.record_file_baseline(
            conversation_id="conv_snapshot",
            run_id="run_snapshot",
            path="hello.txt",
            existed=True,
            blob_hash=None,
            size=6,
        )
        store.record_file_change(
            conversation_id="conv_snapshot",
            run_id="run_snapshot",
            path="hello.txt",
            kind="modify",
            before_hash="sha256:before",
            after_hash="sha256:after",
            patch="--- a/hello.txt\n+++ b/hello.txt\n-old\n+hello changed",
            additions=1,
            deletions=1,
            capture_grade="verified",
        )
        store.create_verification_attempt(
            conversation_id="conv_snapshot",
            run_id="run_snapshot",
            command=["git", "diff", "--check"],
            status="passed",
            exit_code=0,
            duration_ms=12,
            summary="passed",
        )


def _seed_reviewable_delivery(workspace, filename: str = "delivery.txt") -> dict[str, str]:
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    engine = RunEngine(workspace)
    service = CollaborationService(
        ConversationService(engine, engine.store),
        engine.store,
    )
    try:
        detail = service.create(
            f"Create {filename}",
            mode="direct",
            agent_id="mock",
        )
        conversation_id = str(detail["conversation"]["conversation_id"])
        primary = detail["assignments"][0]
        main_session_id = str(detail["sessions"][0]["session_id"])
        (Path(primary["worktree"]) / filename).write_text(
            "trusted\n", encoding="utf-8"
        )
        service.report(
            str(primary["assignment_id"]),
            {
                "summary": "implemented",
                "deliverables": [filename],
                "proof": ["changeset"],
            },
        )
        reviewer = next(
            item
            for item in service.store.list_assignments(conversation_id)
            if item["dispatch_kind"] == "review"
        )
        service.report(
            str(reviewer["assignment_id"]),
            {
                "summary": "independent review passed",
                "status": "passed",
                "deliverables": ["review"],
                "proof": ["review manifest"],
                "findings": [],
            },
        )
        candidate = service.store.delivery_candidates(conversation_id)[-1]
        return {
            "conversation_id": conversation_id,
            "candidate_id": str(candidate["candidate_id"]),
            "run_id": str(candidate["run_id"]),
            "main_session_id": main_session_id,
        }
    finally:
        engine.store.close()


def _close_conversation_sessions(workspace, conversation_id: str) -> None:
    manager = agent_session_manager(workspace)
    with ControlStore(workspace) as store:
        sessions = store.list_agent_sessions(conversation_id)
    for session in sessions:
        if session["status"] not in {"closed", "failed"}:
            manager.close(str(session["session_id"]))


def test_conversation_snapshot_changes_review_and_file_boundaries(workspace) -> None:
    _seed_conversation(workspace)
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    snapshot = client.get(
        f"{api}/conversations/conv_snapshot/snapshot"
    ).json()
    assert snapshot["schema_version"] == "muxdev.conversation-snapshot.v1"
    assert snapshot["attention"] == "ready"
    assert snapshot["timeline"][-1]["capture_grade"] == "verified"
    assert snapshot["last_sequence"] == 1
    assert client.get(f"{api}/conversations/conv_snapshot/room").status_code == 404

    changes = client.get(f"{api}/conversations/conv_snapshot/changes").json()
    assert changes["verified"] is True
    assert changes["files"][0]["path"] == "hello.txt"

    review = client.get(f"{api}/conversations/conv_snapshot/review").json()
    assert review["verification_attempts"][0]["freshness"] == "current"

    current = client.get(
        f"{api}/conversations/conv_snapshot/file",
        params={"path": "hello.txt", "view": "current"},
    )
    assert current.status_code == 200
    assert current.json()["content"].replace("\r\n", "\n") == "hello changed\n"

    traversal = client.get(
        f"{api}/conversations/conv_snapshot/file",
        params={"path": "../outside.txt", "view": "current"},
    )
    assert traversal.status_code == 400


def test_conversation_window_never_returns_more_than_requested_limit(workspace) -> None:
    _seed_conversation(workspace)
    with ControlStore(workspace) as store:
        for index in range(25):
            store.append_conversation_event(
                "conv_snapshot",
                "test.event",
                {"index": index},
                actor="runtime",
            )
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    payload = client.get(
        f"{api}/conversations/conv_snapshot/snapshot",
        params={"limit": 10},
    ).json()
    assert len(payload["timeline"]) == 10
    assert payload["timeline"][-1]["sequence"] == 26


def test_stream_resume_starts_strictly_after_cursor(workspace) -> None:
    _seed_conversation(workspace)
    with ControlStore(workspace) as store:
        store.append_conversation_event(
            "conv_snapshot",
            "test.second",
            {"index": 2},
            actor="runtime",
        )

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    async def read_one() -> str:
        stream = _stream_events(
            workspace,
            "conv_snapshot",
            ConnectedRequest(),
            1,
        )
        try:
            return await anext(stream)
        finally:
            await stream.aclose()

    frame = asyncio.run(read_one())
    assert "id: 2" in frame
    assert '"sequence":2' in frame
    assert '"sequence":1' not in frame


def test_conversation_reads_only_latest_200_of_10000_events_under_budget(
    workspace,
) -> None:
    with ControlStore(workspace) as store:
        store.create_conversation(
            conversation_id="conv_large",
            title="large",
            goal="large timeline",
            status="idle",
            metadata={"worktree": str(workspace)},
            mode="direct",
        )
        rows = [
            (
                f"event_{index}",
                "conv_large",
                "runtime",
                "runtime",
                "runtime",
                "test.event",
                index,
                "2026-01-01T00:00:00+00:00",
                json.dumps({"index": index}, separators=(",", ":")),
                "sha256:" + "0" * 64,
                f"sha256:{index:064x}",
            )
            for index in range(1, 10_001)
        ]
        store.connection.executemany(
            """INSERT INTO conversation_events(
              event_id, conversation_id, actor, actor_kind, actor_id,
              schema_version, type, sequence, created_at, capture_grade,
              payload, previous_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, 2, ?, ?, ?, 'recorded', ?, ?, ?)""",
            rows,
        )
        store.connection.commit()
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    started = time.perf_counter()
    response = client.get(f"{api}/conversations/conv_large/snapshot")
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert len(response.json()["timeline"]) == 200
    assert response.json()["last_sequence"] == 10_000
    assert elapsed < 0.5


def test_replay_distinguishes_verified_events_and_memory_requires_approval(workspace) -> None:
    _seed_conversation(workspace)
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    replay = client.get(
        f"{api}/conversations/conv_snapshot/replay",
        params={"depth": "verify"},
    ).json()
    assert replay["chain"]["valid"] is True
    assert replay["counts"]["verified"] == 1
    assert all(item["capture_grade"] == "verified" for item in replay["events"])

    created = client.post(
        f"{api}/conversations/conv_snapshot/memory/candidates",
        json={"rule": "Always run the focused regression test before review."},
    )
    assert created.status_code == 201
    candidate_id = created.json()["candidate_id"]
    assert not (workspace / "MUXDEV.md").exists()

    approved = client.post(
        f"{api}/conversations/conv_snapshot/memory/candidates/{candidate_id}/approve"
    )
    assert approved.status_code == 200
    assert not (workspace / "MUXDEV.md").exists()
    rules = client.get("/api/v2/rules").json()
    assert any(
        "focused regression test" in item["instructions"] for item in rules
    )


def test_preview_rejects_malformed_or_non_loopback_urls_before_session_lookup(
    workspace,
) -> None:
    client = TestClient(create_app(workspace))
    api = _project_api(client)

    malformed = client.post(
        f"{api}/sessions/missing/preview",
        json={
            "url": "http://localhost:99999",
            "process_id": "1",
            "cwd": str(workspace),
        },
    )
    remote = client.post(
        f"{api}/sessions/missing/preview",
        json={
            "url": "https://example.com:443",
            "process_id": "1",
            "cwd": str(workspace),
        },
    )

    assert malformed.status_code == 422
    assert remote.status_code == 422


def test_request_changes_starts_next_turn_in_the_same_main_session(workspace) -> None:
    seeded = _seed_reviewable_delivery(workspace)
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    try:
        response = client.post(
            f"{api}/conversations/{seeded['conversation_id']}/review/request-changes",
            json={
                "message": "Add the missing edge case.",
                "references": [{"path": "delivery.txt", "line": 1}],
            },
        )
        assert response.status_code == 200, response.text

        with ControlStore(workspace) as store:
            conversation = store.get_conversation(seeded["conversation_id"]) or {}
            old_run = store.get_run(seeded["run_id"]) or {}
            old_candidate = store.get_delivery_candidate(seeded["candidate_id"]) or {}
            assignments = store.list_assignments(seeded["conversation_id"])
            direct = [item for item in assignments if item["dispatch_kind"] == "direct"]
            sessions = store.list_agent_sessions(seeded["conversation_id"])
            events = store.conversation_events(seeded["conversation_id"])

        assert conversation["status"] == "working"
        assert conversation["active_run_id"] != seeded["run_id"]
        assert old_run["review_state"] == "superseded"
        assert old_candidate["status"] == "invalidated"
        assert len(direct) == 2
        assert direct[-1]["run_id"] == conversation["active_run_id"]
        primary_sessions = [
            item
            for item in sessions
            if item["lane_key"] == "main" and item["agent_id"] == "mock"
        ]
        assert len(primary_sessions) == 1
        assert primary_sessions[0]["session_id"] == seeded["main_session_id"]
        assert any(
            item["type"] == "review.changes_requested" for item in events
        )
        assert events[-1]["type"] == "memory.checkpoint_created"
    finally:
        _close_conversation_sessions(workspace, seeded["conversation_id"])


def test_discard_rolls_back_bytes_records_activity_and_settles_review(workspace) -> None:
    seeded = _seed_reviewable_delivery(workspace)
    client = TestClient(create_app(workspace))
    api = _project_api(client)
    try:
        response = client.post(
            f"{api}/deliveries/{seeded['candidate_id']}/discard"
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "rolled_back"
        assert not (workspace / "delivery.txt").exists()

        review = client.get(
            f"{api}/conversations/{seeded['conversation_id']}/review",
            params={"run_id": seeded["run_id"]},
        )
        assert review.status_code == 200, review.text
        assert review.json()["review_state"] == "rolled_back"
        assert "已安全回退" in review.json()["outcome"]
        assert review.json()["available_actions"] == []

        with ControlStore(workspace) as store:
            events = store.conversation_events(seeded["conversation_id"])
            conversation = store.get_conversation(seeded["conversation_id"]) or {}
        assert conversation["status"] == "idle"
        rollback = next(
            item for item in events if item["type"] == "workspace.rolled_back"
        )
        assert rollback["capture_grade"] == "verified"
        assert events[-1]["type"] == "memory.checkpoint_created"
    finally:
        _close_conversation_sessions(workspace, seeded["conversation_id"])
