"""Deterministic Conversation memory checkpoints with provenance."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from .control import ControlStore


DEFAULT_TAIL_TOKEN_THRESHOLD = 8_000


def estimated_tokens(value: object) -> int:
    """Conservative language-agnostic estimate used only for compaction thresholds."""
    rendered = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    ascii_count = sum(1 for character in rendered if ord(character) < 128)
    non_ascii = len(rendered) - ascii_count
    return max(1, ascii_count // 4 + non_ascii)


def checkpoint_source_hash(events: list[Mapping[str, Any]]) -> str:
    payload = "\n".join(str(item.get("event_hash") or "") for item in events)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def ensure_conversation_checkpoint(
    store: ControlStore,
    conversation_id: str,
    *,
    force: bool = False,
    created_by: str = "runtime",
) -> dict[str, Any] | None:
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        raise FileNotFoundError(conversation_id)
    events = store.conversation_events(conversation_id)
    source_events = [
        item for item in events if not str(item.get("type") or "").startswith("memory.")
    ]
    if not source_events:
        return None
    latest = store.latest_memory_checkpoint(conversation_id)
    last_sequence = int(source_events[-1]["sequence"])
    through_sequence = int((latest or {}).get("through_sequence") or 0)
    tail = [item for item in source_events if int(item["sequence"]) > through_sequence]
    if latest and last_sequence <= through_sequence:
        return latest
    if not force and estimated_tokens([
        {"type": item["type"], "payload": item.get("payload")} for item in tail
    ]) < DEFAULT_TAIL_TOKEN_THRESHOLD:
        return latest

    content = _checkpoint_content(store, conversation_id, conversation, source_events)
    checkpoint = store.create_memory_checkpoint(
        conversation_id,
        kind="automatic",
        through_sequence=last_sequence,
        source_hash=checkpoint_source_hash(source_events),
        content=content,
        created_by=created_by,
        parent_checkpoint_id=(
            str(latest["checkpoint_id"]) if latest else None
        ),
        metadata={
            "source_event_count": len(source_events),
            "tail_event_count": len(tail),
            "estimated_tail_tokens": estimated_tokens([
                {"type": item["type"], "payload": item.get("payload")} for item in tail
            ]),
        },
    )
    store.append_conversation_event(
        conversation_id,
        "memory.checkpoint_created",
        {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "version": checkpoint["version"],
            "through_sequence": last_sequence,
            "source_hash": checkpoint["source_hash"],
        },
        actor="runtime",
        capture_grade="recorded",
    )
    return checkpoint


def correct_conversation_checkpoint(
    store: ControlStore,
    conversation_id: str,
    correction: str,
    *,
    actor_id: str = "developer",
) -> dict[str, Any]:
    text = correction.strip()
    if not text:
        raise ValueError("memory correction cannot be empty")
    latest = ensure_conversation_checkpoint(
        store, conversation_id, force=True, created_by="runtime"
    )
    if not latest:
        raise RuntimeError("Conversation has no facts to correct")
    content = dict(latest.get("content") or {})
    corrections = list(content.get("corrections") or [])
    corrections.append(
        {
            "text": text,
            "actor_id": actor_id,
            "supersedes_checkpoint_id": latest["checkpoint_id"],
        }
    )
    content["corrections"] = corrections
    checkpoint = store.create_memory_checkpoint(
        conversation_id,
        kind="correction",
        through_sequence=int(latest["through_sequence"]),
        source_hash=str(latest["source_hash"]),
        content=content,
        created_by=actor_id,
        parent_checkpoint_id=str(latest["checkpoint_id"]),
        metadata={"correction": text},
    )
    store.append_conversation_event(
        conversation_id,
        "memory.corrected",
        {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "parent_checkpoint_id": latest["checkpoint_id"],
            "version": checkpoint["version"],
            "correction": text,
        },
        actor=actor_id,
        capture_grade="verified",
    )
    return checkpoint


def _checkpoint_content(
    store: ControlStore,
    conversation_id: str,
    conversation: Mapping[str, Any],
    events: list[Mapping[str, Any]],
) -> dict[str, Any]:
    contract = None
    if conversation.get("active_contract_id"):
        contract = store.get_delivery_contract(str(conversation["active_contract_id"]))
    runs = store.list_conversation_runs(conversation_id)
    assignments = store.list_assignments(conversation_id)
    interactions = store.list_conversation_interactions(conversation_id)
    decisions = store.list_review_records(conversation_id)
    completed_assignments = [
        {
            "assignment_id": item.get("assignment_id"),
            "agent_id": item.get("agent_id"),
            "title": item.get("title"),
            "status": item.get("status"),
            "report": (item.get("metadata") or {}).get("report"),
            "changeset_digest": item.get("changeset_digest"),
        }
        for item in assignments
        if item.get("status") in {"reported", "ready_to_merge", "completed"}
    ]
    unresolved = [
        {
            "interaction_id": item.get("interaction_id"),
            "prompt": item.get("prompt"),
            "options": item.get("options"),
        }
        for item in interactions
        if item.get("status") == "pending"
    ]
    return {
        "schema_version": "muxdev.conversation-memory.v1",
        "conversation": {
            "conversation_id": conversation_id,
            "title": conversation.get("title"),
            "goal": conversation.get("goal"),
            "status": conversation.get("status"),
        },
        "contract": {
            "contract_id": (contract or {}).get("contract_id"),
            "goal": (contract or {}).get("goal"),
            "acceptance_criteria": (contract or {}).get("acceptance_criteria"),
            "allowed_scope": (contract or {}).get("allowed_scope"),
            "profile": (contract or {}).get("profile"),
        },
        "runs": [
            {
                "run_id": item.get("run_id"),
                "turn_index": item.get("turn_index"),
                "status": item.get("status"),
                "review_state": item.get("review_state"),
            }
            for item in runs
        ],
        "assignment_results": completed_assignments,
        "agent_summaries": [
            {
                "assignment_id": item.get("assignment_id"),
                "agent_id": item.get("agent_id"),
                "summary": (
                    ((item.get("metadata") or {}).get("report") or {}).get(
                        "summary"
                    )
                ),
                "trust_grade": "observed",
            }
            for item in assignments
            if ((item.get("metadata") or {}).get("report") or {}).get("summary")
        ],
        "decisions": [
            {
                "review_id": item.get("review_id"),
                "kind": item.get("kind"),
                "status": item.get("status"),
                "prompt": item.get("prompt"),
                "response": item.get("response"),
                "references": item.get("references_json"),
            }
            for item in decisions
            if item.get("status") not in {"pending", "open"}
        ],
        "open_items": unresolved,
        "recent_facts": [
            {
                "sequence": item.get("sequence"),
                "type": item.get("type"),
                "actor": item.get("actor"),
                "payload": item.get("payload"),
                "capture_grade": item.get("capture_grade") or "recorded",
            }
            for item in events[-50:]
            if item.get("type")
            in {
                "user.message",
                "requirements.ready",
                "interaction.responded",
                "assignment.reported",
                "delivery.accepted",
                "workspace.rolled_back",
                "review.changes_requested",
            }
        ],
        "corrections": list(
            dict(
                (store.latest_memory_checkpoint(conversation_id) or {}).get(
                    "content"
                )
                or {}
            ).get("corrections", [])
        ),
    }


__all__ = [
    "DEFAULT_TAIL_TOKEN_THRESHOLD",
    "checkpoint_source_hash",
    "correct_conversation_checkpoint",
    "ensure_conversation_checkpoint",
    "estimated_tokens",
]
