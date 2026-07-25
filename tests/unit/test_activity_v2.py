from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from muxdev.storage import ControlStore


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_v2_activity_continues_a_legacy_hash_chain(workspace) -> None:
    with ControlStore(workspace) as store:
        store.create_conversation(
            conversation_id="conv_activity",
            title="activity",
            goal="verify activity",
            status="working",
            metadata={},
            mode="direct",
        )
        previous_hash = "sha256:" + "0" * 64
        fact = {
            "event_id": "legacy_event",
            "conversation_id": "conv_activity",
            "run_id": None,
            "run_event_id": None,
            "actor": "developer",
            "type": "user.message",
            "sequence": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "payload": {"content": "legacy"},
            "previous_hash": previous_hash,
        }
        event_hash = "sha256:" + hashlib.sha256(_json(fact).encode()).hexdigest()
        store.connection.execute(
            """INSERT INTO conversation_events(
              event_id, conversation_id, run_id, run_event_id, actor, actor_kind,
              actor_id, schema_version, type, sequence, created_at, capture_grade,
              payload, previous_hash, event_hash
            ) VALUES (?, ?, NULL, NULL, ?, ?, ?, 1, ?, ?, ?, 'recorded', ?, ?, ?)""",
            (
                "legacy_event",
                "conv_activity",
                "developer",
                "developer",
                "developer",
                "user.message",
                1,
                "2026-01-01T00:00:00+00:00",
                _json({"content": "legacy"}),
                previous_hash,
                event_hash,
            ),
        )
        store.connection.commit()

        store.append_conversation_event(
            "conv_activity",
            "workspace.reconciled",
            {"file_count": 1},
            actor="runtime",
            capture_grade="verified",
        )

        valid, errors = store.verify_conversation_event_chain("conv_activity")
        activity = store.conversation_activity("conv_activity")

    assert valid, errors
    assert [item["sequence"] for item in activity] == [1, 2]
    assert activity[0]["payload"]["_legacy_schema_version"] == 1
    assert activity[1]["schema_version"] == 2
    assert activity[1]["actor"] == {"kind": "runtime", "id": "runtime"}
    assert activity[1]["capture_grade"] == "verified"


def test_concurrent_activity_publishers_allocate_unique_sequences(workspace) -> None:
    with ControlStore(workspace) as store:
        store.create_conversation(
            conversation_id="conv_concurrent",
            title="concurrent",
            goal="append safely",
            status="working",
            metadata={},
            mode="direct",
        )

    def append(index: int) -> None:
        with ControlStore(workspace) as worker:
            worker.append_conversation_event(
                "conv_concurrent",
                "test.event",
                {"index": index},
                actor="runtime",
            )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, range(24)))

    with ControlStore(workspace) as store:
        events = store.conversation_events("conv_concurrent")
        valid, errors = store.verify_conversation_event_chain("conv_concurrent")
    assert valid, errors
    assert [item["sequence"] for item in events] == list(range(1, 25))
