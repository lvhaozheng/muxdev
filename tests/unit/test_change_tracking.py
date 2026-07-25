from __future__ import annotations

from pathlib import Path

import pytest

from muxdev.runtime.change_tracking import (
    ChangeTrackingContext,
    ChangeTrackingService,
    RollbackConflictError,
)
from muxdev.storage import ControlStore


def _store_with_run(workspace: Path) -> ControlStore:
    store = ControlStore(workspace)
    store.create_conversation(
        conversation_id="conv_changes",
        title="changes",
        goal="track files",
        status="working",
        metadata={"worktree": str(workspace)},
        mode="direct",
    )
    store.create_run(
        run_id="run_changes",
        run_kind="assignment",
        conversation_id="conv_changes",
        task="track files",
        workflow="change",
        profile="standard",
        provider="mock",
        policy_hash="sha256:test",
        metadata={"worktree": str(workspace)},
    )
    store.update_conversation("conv_changes", active_run_id="run_changes")
    return store


def test_change_tracking_reconciles_and_restores_exact_baseline(workspace: Path) -> None:
    target = workspace / "notes.txt"
    target.write_bytes(b"before\r\n")
    store = _store_with_run(workspace)
    context = ChangeTrackingContext(
        conversation_id="conv_changes",
        run_id="run_changes",
        worktree=workspace,
        assignment_id="asn_test",
        author="mock",
    )
    tracker = ChangeTrackingService(workspace, store)
    tracker.capture_baseline(context)

    target.write_bytes(b"after\nsecond\n")
    observed = tracker.observe_paths(context, [target])
    verified = tracker.reconcile(context)

    assert observed == 1
    assert len(verified) == 1
    assert verified[0]["path"] == "notes.txt"
    assert verified[0]["capture_grade"] == "verified"
    assert "+after" in verified[0]["patch"]

    restored = tracker.rollback(context)
    assert restored == ["notes.txt"]
    assert target.read_bytes() == b"before\r\n"
    store.close()


def test_rollback_fails_closed_after_later_user_edit(workspace: Path) -> None:
    target = workspace / "notes.txt"
    target.write_text("before\n", encoding="utf-8")
    store = _store_with_run(workspace)
    context = ChangeTrackingContext(
        conversation_id="conv_changes",
        run_id="run_changes",
        worktree=workspace,
    )
    tracker = ChangeTrackingService(workspace, store)
    tracker.capture_baseline(context)
    target.write_text("agent\n", encoding="utf-8")
    tracker.reconcile(context)
    target.write_text("developer changed this later\n", encoding="utf-8")

    with pytest.raises(RollbackConflictError):
        tracker.rollback(context)

    assert target.read_text(encoding="utf-8") == "developer changed this later\n"
    store.close()


def test_sensitive_files_never_store_text_patch(workspace: Path) -> None:
    target = workspace / ".env"
    target.write_text("TOKEN=before\n", encoding="utf-8")
    store = _store_with_run(workspace)
    context = ChangeTrackingContext(
        conversation_id="conv_changes",
        run_id="run_changes",
        worktree=workspace,
    )
    tracker = ChangeTrackingService(workspace, store)
    tracker.capture_baseline(context)
    target.write_text("TOKEN=after\n", encoding="utf-8")
    records = tracker.reconcile(context)

    assert records[0]["patch"] == ""
    assert records[0]["before_hash"]
    assert records[0]["after_hash"]
    store.close()
