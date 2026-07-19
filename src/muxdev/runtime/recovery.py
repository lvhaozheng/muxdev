"""Recovery/fork semantics for stage snapshots."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from ..models import WorkflowDefinition, utc_now
from ..storage import Blackboard
from ..storage.contracts import write_json_artifact


def downstream_stage_ids(workflow: WorkflowDefinition, from_stage: str) -> list[str]:
    by_id = {stage.id: stage for stage in workflow.stages}
    if from_stage not in by_id:
        raise ValueError(f"stage is not present in archived workflow: {from_stage}")
    selected = {from_stage}
    changed = True
    while changed:
        changed = False
        for stage in workflow.stages:
            if stage.id not in selected and any(dep in selected for dep in stage.deps):
                selected.add(stage.id)
                changed = True
    return [stage.id for stage in workflow.stages if stage.id in selected]


def finalize_stage_recovery(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    workflow: WorkflowDefinition,
    from_stage: str,
    snapshot_path: Path,
    snapshot_hash: str,
) -> dict[str, object]:
    recovery_id = f"recovery_{uuid4().hex}"
    invalidated = downstream_stage_ids(workflow, from_stage)
    marker_path, marker_hash = write_json_artifact(
        run_dir / "recovery" / f"{recovery_id}.json",
        {
            "contract_version": "muxdev.recovery.v1",
            "recovery_id": recovery_id,
            "run_id": run_id,
            "from_stage": from_stage,
            "invalidated_stages": invalidated,
            "snapshot": {"path": str(snapshot_path), "patch_hash": snapshot_hash},
            "semantics": "fork_from_snapshot; immutable history is retained and current delivery projections are invalidated",
            "created_at": utc_now(),
        },
    )
    invalidated_projection = blackboard.record_recovery_fork(
        run_id,
        from_stage=from_stage,
        invalidated_stages=invalidated,
        snapshot_path=snapshot_path,
        snapshot_hash=snapshot_hash,
        recovery_id=recovery_id,
    )
    blackboard.add_artifact(run_id, from_stage, marker_path.name, marker_path, "recovery")
    blackboard.add_checkpoint(run_id, from_stage, "recovery_forked")
    return {
        "recovery_id": recovery_id,
        "recovery_event": str(marker_path),
        "recovery_event_hash": marker_hash,
        "invalidated_stages": invalidated,
        "invalidated_projection": invalidated_projection,
    }
