"""Materialize replayable whole-file payloads for ChangeSet operations."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from ..storage.control import ControlStore
from .workspace import ChangeSet


def store_changeset_payloads(
    store: ControlStore,
    run_id: str,
    run_dir: Path,
    worktree: Path,
    change_set: ChangeSet,
) -> ChangeSet:
    """Persist complete post-change files so binary operations remain replayable."""
    payload_root = (run_dir / "artifacts" / "changeset").resolve()
    operations = []
    for operation in change_set.operations:
        if operation.operation == "delete":
            operations.append(operation)
            continue
        source = (worktree / operation.path).resolve()
        target = (payload_root / operation.path).resolve()
        if payload_root not in target.parents or not source.is_file() or source.is_symlink():
            raise ValueError(f"unsafe ChangeSet payload path: {operation.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        artifact = store.add_artifact(
            run_id,
            name=f"changeset/{operation.path}",
            path=target,
            kind="changeset_file",
            media_type="application/octet-stream",
        )
        operations.append(replace(operation, artifact=f"artifact:{artifact['artifact_id']}"))
    return ChangeSet(change_set.before_digest, change_set.after_digest, tuple(operations))
