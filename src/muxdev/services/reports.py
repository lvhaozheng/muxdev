"""Final-report service facade."""

from __future__ import annotations

from pathlib import Path

from ..storage import Blackboard
from .deliverables import workflow_deliverable_status


def generate_final_report(run_dir: Path, run_id: str, blackboard: Blackboard) -> Path:
    run = blackboard.get_run(run_id)
    stages = blackboard.table_rows("stages", run_id=run_id)
    approvals = blackboard.table_rows("approvals", run_id=run_id)
    tests = blackboard.table_rows("test_results", run_id=run_id)
    blockers = blackboard.table_rows("review_blockers", run_id=run_id)
    artifacts = sorted(blackboard.table_rows("artifacts", run_id=run_id), key=_artifact_sort_key)
    checkpoints = blackboard.table_rows("checkpoints", run_id=run_id)
    errors = blackboard.table_rows("error_details", run_id=run_id)
    ledger_events = blackboard.table_rows("ledger_events", run_id=run_id)
    snapshots = blackboard.table_rows("snapshots", run_id=run_id)
    validators = blackboard.table_rows("validator_panels", run_id=run_id)
    manifests = blackboard.table_rows("evidence_manifests", run_id=run_id)
    evaluations = blackboard.table_rows("evidence_evaluations", run_id=run_id)
    deliverable_status = workflow_deliverable_status(
        blackboard,
        run_dir=run_dir,
        run_id=run_id,
        workflow=str(run.get("workflow") or ""),
        require_report=False,
    )
    lines = [
        f"# muxdev final report: {run_id}",
        "",
        f"- Task: {run['task']}",
        f"- Workflow: {run['workflow']}",
        f"- Provider: {run['provider']}",
        f"- Status: {run['status']}",
        f"- Worktree: {run['worktree']}",
        "",
    ]
    if evaluations:
        evaluation = evaluations[-1]
        manifest = manifests[-1] if manifests else {}
        lines.extend(
            [
                "## Evidence Evaluation",
                f"- Label: {evaluation['label']}",
                f"- Confidence: {evaluation['confidence']}",
                f"- Events: {manifest.get('event_count', 0)}",
                f"- Head hash: {manifest.get('head_hash') or '-'}",
                "- Missing evidence: " + (", ".join(str(item) for item in evaluation.get("missing_evidence", [])) or "none"),
                "",
            ]
        )
    lines.extend(["## Design Deliverables"])
    design_artifacts = [artifact for artifact in artifacts if artifact.get("kind") == "project_design_doc"]
    if design_artifacts:
        for artifact in design_artifacts:
            lines.append(f"- {artifact['name']}: {artifact['path']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Workflow Deliverables"])
    for item in deliverable_status.get("required", []):
        marker = "ready" if item in deliverable_status.get("ready", []) else "missing"
        lines.append(f"- {item}: {marker}")
    lines.extend(["", "## Stage Timeline"])
    for stage in stages:
        lines.append(f"- {stage['stage_id']}: {stage['status']} - {stage['summary'] or ''}")
    lines.extend(["", "## Test Results"])
    for result in tests:
        status = "passed" if result["passed"] else "failed"
        lines.append(f"- {result['command']}: {status} - {result['summary']}")
    lines.extend(["", "## Review Blockers"])
    if blockers:
        for blocker in blockers:
            lines.append(f"- {blocker['severity']} {blocker['type']}: {blocker['suggestion']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Approvals"])
    if approvals:
        for approval in approvals:
            lines.append(f"- {approval['approval_id']}: {approval['type']} {approval['status']} - {approval['reason']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Checkpoints"])
    if checkpoints:
        for checkpoint in checkpoints:
            lines.append(f"- {checkpoint['stage_id'] or 'run'}: {checkpoint['kind']} at {checkpoint['created_at']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Errors"])
    if errors:
        for error in errors:
            lines.append(f"- {error['stage_id'] or 'run'} {error['type']}: {error['message']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Artifacts"])
    for artifact in artifacts:
        lines.append(f"- {artifact['name']}: {artifact['path']}")
    lines.extend(["", "## Evidence v2 Integrity"])
    lines.append(f"- events: {manifests[-1].get('event_count', 0) if manifests else 0}")
    lines.append(f"- ledger events: {len(ledger_events)}")
    lines.append(f"- snapshots: {len(snapshots)}")
    if validators:
        for validator in validators:
            lines.append(f"- validator {validator['validator_id']}: {validator['decision']} {validator['validator_hash']}")
    else:
        lines.append("- validators: none")
    path = run_dir / "final_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    blackboard.add_artifact(run_id, None, "final_report.md", path, "report")
    return path


def _artifact_sort_key(row: dict[str, object]) -> tuple[int, str, str]:
    priority = {
        "project_design_doc": 0,
        "report": 1,
        "diff": 2,
        "plan_summary": 3,
        "test_report": 4,
        "review_report": 5,
        "handoff_summary": 6,
        "docs_report": 7,
        "stage_output": 10,
        "task": 90,
        "context": 91,
    }
    kind = str(row.get("kind") or "")
    name = str(row.get("name") or "")
    return (priority.get(kind, 50), kind, name)
