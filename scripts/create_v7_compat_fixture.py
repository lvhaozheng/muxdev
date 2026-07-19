"""Regenerate the frozen pre-refactor v7 compatibility fixture.

This script is intentionally not part of normal test execution. Reviewers can
run it only when the v7 compatibility contract itself is deliberately changed.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from muxdev.storage import Blackboard


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "pre_refactor_v7"
RUN_ID = "run_pre_refactor_v7"


def main() -> None:
    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)
    FIXTURE.mkdir(parents=True)
    evidence_dir = FIXTURE / "evidence"
    evidence_dir.mkdir()
    manifest_path = evidence_dir / "manifest.json"
    evaluation_path = evidence_dir / "evaluation.json"
    artifact_path = FIXTURE / "design.md"
    artifact_path.write_text("# Historical design\n\nReadable after the refactor.\n", encoding="utf-8")
    manifest = {
        "head_hash": "sha256:legacy-event",
        "event_count": 1,
        "artifact_count": 1,
        "required_matrix": {"design": "present"},
        "missing_required": [],
        "created_at": "2026-07-18T00:00:00Z",
    }
    evaluation = {
        "label": "trusted",
        "confidence": 0.9,
        "gates": {"design": True},
        "components": {"evidence": 1.0},
        "reasons": ["frozen compatibility fixture"],
        "missing_evidence": [],
        "next_actions": [],
        "created_at": "2026-07-18T00:00:00Z",
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    evaluation_path.write_text(json.dumps(evaluation, sort_keys=True), encoding="utf-8")

    with Blackboard(FIXTURE, db_path=FIXTURE / "blackboard.sqlite") as board:
        board.create_run(
            run_id=RUN_ID,
            task="resume a pre-refactor v7 task",
            workflow="software-dev",
            provider="mock",
            workspace=FIXTURE,
            worktree=FIXTURE,
        )
        board.set_run_status(RUN_ID, "running", idempotency_key="fixture:running")
        board.upsert_stage(RUN_ID, "design", role="architect", status="completed", output_path=str(artifact_path), summary="legacy stage complete")
        board.add_artifact(RUN_ID, "design", artifact_path.name, artifact_path, "stage_output")
        board.replace_evidence_v2(
            run_id=RUN_ID,
            events=[
                {
                    "id": "evt_pre_refactor",
                    "stage_id": "design",
                    "layer": "stage",
                    "kind": "artifact",
                    "claim": "historical artifact exists",
                    "status": "passed",
                    "strength": "strong",
                    "subject_hash": "sha256:legacy-artifact",
                    "prev_hash": None,
                    "event_hash": "sha256:legacy-event",
                    "artifact_refs": ["design.md"],
                    "metrics": {},
                    "tags": ["v7"],
                    "source": "pre-refactor",
                    "created_at": "2026-07-18T00:00:00Z",
                }
            ],
            manifest=manifest,
            manifest_path=manifest_path,
            manifest_hash="sha256:legacy-manifest",
            evaluation=evaluation,
            evaluation_path=evaluation_path,
            evaluation_hash="sha256:legacy-evaluation",
        )
        board.set_run_status(RUN_ID, "blocked", idempotency_key="fixture:blocked")


if __name__ == "__main__":
    main()
