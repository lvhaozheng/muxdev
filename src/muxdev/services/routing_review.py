"""Policy and frozen-input helpers for heterogeneous read-only review."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..core.platforms import hidden_subprocess_kwargs
from ..domain.routing import REVIEW_CONTRACT_VERSION, ReviewAssignment
from ..domain.run import HarnessPolicySpec, RoutingPolicySpec
from ..models import ApprovalStatus
from ..storage.contracts import canonical_hash, sha256_file, sha256_text, write_json_artifact


@dataclass(frozen=True)
class ReviewerPreflight:
    status: str
    assignment: Mapping[str, Any] | None
    approval_id: str | None = None
    reason: str = ""


def incomplete_current_reviews(board: Any, run_id: str) -> list[dict[str, Any]]:
    """Return only the latest attempt for the current route when it is incomplete."""
    decision = board.latest_route_decision(run_id, kind="main")
    if decision is None:
        return []
    decision_id = str(decision.get("decision_id") or "")
    assignments = [
        item
        for item in board.list_review_assignments(run_id)
        if str(item.get("route_decision_id") or "") == decision_id
    ]
    if not assignments:
        return []
    latest = max(
        assignments,
        key=lambda item: (int(item.get("attempt") or 1), str(item.get("updated_at") or "")),
    )
    return [] if str(latest.get("status") or "") in {"completed", "waived"} else [latest]


def ensure_reviewer_policy(
    board: Any,
    *,
    run_id: str,
    decision: Any,
    harness_policy: HarnessPolicySpec,
    routing_policy: RoutingPolicySpec,
) -> ReviewerPreflight:
    required = routing_policy.reviewer_policy == "required" or harness_policy.risk_level == "high"
    if not required:
        return ReviewerPreflight(status="ready", assignment=None)
    assignments = board.list_review_assignments(run_id)
    current = next((item for item in assignments if str(item.get("route_decision_id")) == decision.decision_id), None)
    if decision.selected_reviewer_provider:
        if current is None:
            current = board.record_review_assignment(
                ReviewAssignment.create(
                    run_id=run_id,
                    route_decision_id=decision.decision_id,
                    reviewer_provider=decision.selected_reviewer_provider,
                    main_provider=str(decision.selected_main_provider or "unknown"),
                    status="assigned",
                )
            )
        return ReviewerPreflight(status="ready", assignment=current)

    subject = {
        "contract_version": "muxdev.heterogeneous-review-waiver.v1",
        "run_id": run_id,
        "route_decision_id": decision.decision_id,
        "decision_hash": decision.decision_hash,
        "feature_hash": decision.feature_hash,
        "main_provider": decision.selected_main_provider,
        "risk_level": harness_policy.risk_level,
        "risk_tags": sorted(harness_policy.risk_tags),
        "reviewer_policy": routing_policy.reviewer_policy,
        "candidate_exclusions": [
            {"provider": candidate.provider, "exclusion_codes": list(candidate.exclusion_codes)}
            for candidate in decision.candidates
            if candidate.provider != decision.selected_main_provider
        ],
    }
    subject_hash = canonical_hash(subject)
    approval = board.find_approval(run_id, None, "heterogeneous_review_unavailable")
    if approval and str(approval.get("subject_hash") or "") == subject_hash:
        status = str(approval.get("status") or "")
        if status == str(ApprovalStatus.APPROVED):
            if current is None:
                current = board.record_review_assignment(
                    ReviewAssignment.create(
                        run_id=run_id,
                        route_decision_id=decision.decision_id,
                        reviewer_provider=None,
                        main_provider=str(decision.selected_main_provider or "unknown"),
                        status="waived",
                        waiver_approval_id=str(approval["approval_id"]),
                    )
                )
            return ReviewerPreflight(status="ready", assignment=current, approval_id=str(approval["approval_id"]), reason="explicit reviewer waiver")
        if status == str(ApprovalStatus.DENIED):
            return ReviewerPreflight(status="blocked", assignment=None, approval_id=str(approval["approval_id"]), reason="heterogeneous review waiver denied")
        return ReviewerPreflight(status="awaiting_approval", assignment=None, approval_id=str(approval["approval_id"]), reason="heterogeneous reviewer is unavailable")
    approval_id = board.create_approval(
        run_id,
        None,
        "heterogeneous_review_unavailable",
        "No certified heterogeneous read-only reviewer is available. Approving waives independent review for this high-risk task.",
        subject=subject,
    )
    return ReviewerPreflight(status="awaiting_approval", assignment=None, approval_id=approval_id, reason="heterogeneous reviewer is unavailable")


def prepare_review_snapshot(
    board: Any,
    *,
    run_dir: Path,
    run_id: str,
    stage_id: str,
    worktree: Path,
    reviewer_provider: str,
) -> tuple[dict[str, Any], Path]:
    assignment = next(
        (
            item for item in reversed(board.list_review_assignments(run_id))
            if str(item.get("reviewer_provider") or "") == reviewer_provider and str(item.get("status")) != "waived"
        ),
        None,
    )
    if assignment is None:
        raise RuntimeError("review stage has no certified heterogeneous assignment")
    if str(assignment.get("status") or "") == "blocked":
        next_attempt = int(assignment.get("attempt") or 1) + 1
        if next_attempt > 2:
            raise RuntimeError("heterogeneous review retry limit exceeded")
        assignment = board.record_review_assignment(
            ReviewAssignment.create(
                run_id=run_id,
                route_decision_id=str(assignment["route_decision_id"]),
                reviewer_provider=reviewer_provider,
                main_provider=str(assignment["main_provider"]),
                status="assigned",
                attempt=next_attempt,
            )
        )
    patch_text = _git_diff(worktree)
    tests = [
        {
            "stage_id": row.get("stage_id"), "passed": bool(row.get("passed")),
            "command_hash": sha256_text(str(row.get("command") or "")),
            "summary_hash": sha256_text(str(row.get("summary") or "")),
        }
        for row in board.table_rows("test_results", run_id=run_id)
    ]
    contracts = [
        {"stage_id": row.get("stage_id"), "contract_hash": row.get("contract_hash")}
        for row in board.table_rows("stage_contracts", run_id=run_id)
    ]
    artifacts: list[dict[str, object]] = []
    for row in board.table_rows("artifacts", run_id=run_id):
        path = Path(str(row.get("path") or ""))
        if path.exists() and path.is_file():
            artifacts.append({"name": row.get("name"), "hash": sha256_file(path)})
    payload = {
        "contract_version": REVIEW_CONTRACT_VERSION,
        "review_id": assignment["review_id"],
        "run_id": run_id,
        "stage_id": stage_id,
        "attempt": int(assignment.get("attempt") or 1),
        "patch_hash": sha256_text(patch_text),
        "patch_size": len(patch_text.encode("utf-8")),
        "test_results": tests,
        "stage_contracts": contracts,
        "artifacts": artifacts,
        "blind": True,
        "read_only": True,
    }
    payload["snapshot_hash"] = canonical_hash(payload)
    path, _digest = write_json_artifact(
        run_dir / "review" / f"{assignment['review_id']}_{int(assignment.get('attempt') or 1)}.json",
        payload,
    )
    board.update_review_assignment(str(assignment["review_id"]), status="running", snapshot_hash=str(payload["snapshot_hash"]))
    board.add_artifact(run_id, stage_id, path.name, path, "review_snapshot")
    return payload, path


def reviewer_prompt(snapshot: Mapping[str, Any]) -> str:
    public = {
        "contract_version": snapshot.get("contract_version"),
        "review_id": snapshot.get("review_id"),
        "snapshot_hash": snapshot.get("snapshot_hash"),
        "patch_hash": snapshot.get("patch_hash"),
        "test_results": snapshot.get("test_results", []),
        "stage_contracts": snapshot.get("stage_contracts", []),
    }
    return (
        "\n\n# Independent blind read-only review\n"
        "Do not modify any file. Review the frozen patch currently visible in the workspace. "
        "Do not infer or request the main provider identity. Return only the configured ReviewResult JSON.\n"
        + json.dumps(public, ensure_ascii=False, sort_keys=True)
    )


def finish_review_assignment(
    board: Any,
    *,
    run_id: str,
    reviewer_provider: str,
    snapshot_hash: str,
    findings: list[Mapping[str, Any]],
) -> dict[str, Any]:
    assignment = next(
        (
            item for item in reversed(board.list_review_assignments(run_id))
            if str(item.get("reviewer_provider") or "") == reviewer_provider
            and str(item.get("snapshot_hash") or "") == snapshot_hash
        ),
        None,
    )
    if assignment is None:
        raise RuntimeError("review result does not match a frozen assignment")
    blockers = [item for item in findings if str(item.get("severity") or "").lower() in {"high", "critical"}]
    return board.update_review_assignment(
        str(assignment["review_id"]),
        status="blocked" if blockers else "completed",
        verdict="reject" if blockers else "accept",
        findings=findings,
    )


def verify_review_snapshot(path: Path, expected_hash: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    actual = str(payload.pop("snapshot_hash", ""))
    return actual == expected_hash == canonical_hash(payload)


def _git_diff(worktree: Path) -> str:
    completed = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff"],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        **hidden_subprocess_kwargs(),
    )
    return completed.stdout if completed.returncode == 0 else ""
