"""Final-report service facade."""

from __future__ import annotations

from pathlib import Path

from ..storage import Blackboard


def generate_final_report(run_dir: Path, run_id: str, blackboard: Blackboard) -> Path:
    run = blackboard.get_run(run_id)
    stages = blackboard.table_rows("stages")
    approvals = blackboard.table_rows("approvals")
    tests = blackboard.table_rows("test_results")
    blockers = blackboard.table_rows("review_blockers")
    artifacts = blackboard.table_rows("artifacts")
    checkpoints = blackboard.table_rows("checkpoints")
    errors = blackboard.table_rows("error_details")
    stage_contracts = blackboard.table_rows("stage_contracts")
    evidence_bundles = blackboard.table_rows("evidence_bundles")
    ledger_events = blackboard.table_rows("ledger_events")
    snapshots = blackboard.table_rows("snapshots")
    validators = blackboard.table_rows("validator_panels")
    scorecards = blackboard.table_rows("evidence_scorecards", run_id=run_id)
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
    if scorecards:
        scorecard = scorecards[-1]
        lines.extend(
            [
                "## Evidence Scorecard",
                f"- Delivery Confidence: {scorecard['score']} / 100",
                f"- Label: {scorecard['label']}",
                f"- Recommendation: {scorecard['recommendation']}",
                f"- Risk penalty: {scorecard['risk_penalty']}",
                "- Missing evidence: " + (", ".join(str(item) for item in scorecard.get("missing_evidence", [])) or "none"),
                "",
            ]
        )
    lines.append("## Stage Timeline")
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
    lines.extend(["", "## Trusted Delivery Evidence"])
    lines.append(f"- stage contracts: {len(stage_contracts)}")
    lines.append(f"- evidence bundles: {len(evidence_bundles)}")
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
