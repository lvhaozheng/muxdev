"""Run finalization, Evidence persistence, and human-gate helpers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from ..models import RunPolicySnapshot, RunStatus
from ..models.evidence import (
    AnyEvidenceRecord,
    ArtifactEvidence,
    CheckEvidence,
    EvidencePolicy,
    EvidenceReport,
    InteractionEvidence,
    ReviewEvidence,
    RuntimeEvidence,
    canonical_hash,
)
from ..services.evidence_policy import load_evidence_policy
from ..services.gate import evaluate_gate
from .changeset_artifacts import store_changeset_payloads
from .delivery_standards import custom_items, standard_requirement_id
from .policy_snapshot import build_harness_summary, load_policy_snapshot
from .recovery import build_recovery_summary
from .workspace import (
    ChangeSet,
    WorkspaceConflictError,
    WorkspaceSnapshot,
    apply_change_set,
    build_change_set,
    diff_text,
    snapshot_workspace,
)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    run_dir: Path
    report_path: Path


class RunFinalizationMixin:
    """Finalize a run and expose shared Evidence helpers to the runtime mixins."""

    def _policy_snapshot(self, run_id: str) -> RunPolicySnapshot:
        return load_policy_snapshot(self.store, run_id)

    def _finalize(
        self, run_id, run_dir, worktree, route, *, started: float, cost_usd: float = 0.0
    ) -> RunResult:
        subject_digest = self._subject_digest(worktree)
        snapshot = self._policy_snapshot(run_id)
        before = WorkspaceSnapshot.from_dict(snapshot.workspace_manifest)
        after = snapshot_workspace(worktree)
        try:
            change_set = build_change_set(before, after)
            change_set = store_changeset_payloads(
                self.store, run_id, run_dir, worktree, change_set
            )
        except (ValueError, OSError) as exc:
            self._runtime_failure(run_id, "changeset", str(exc))
            change_set = ChangeSet(before.digest, after.digest, ())
        changeset_path = run_dir / "changeset.json"
        changeset_path.write_text(
            json.dumps(change_set.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        changeset_artifact = self.store.add_artifact(
            run_id, name=changeset_path.name, path=changeset_path,
            kind="changeset", media_type="application/json",
        )
        diff_path = run_dir / "diff.patch"
        diff_path.write_text(diff_text(worktree), encoding="utf-8")
        artifact = self.store.add_artifact(
            run_id, name="diff.patch", path=diff_path,
            kind="diff", media_type="text/x-diff",
        )
        requirements = {item.id for item in self._policy(run_id).requirements}
        if "change_artifact" in requirements:
            self._append_evidence(ArtifactEvidence(
                record_id=_record_id(), run_id=run_id, requirement_id="change_artifact",
                subject_digest=subject_digest, producer="muxdev.runtime", path=str(diff_path),
                digest=str(artifact["digest"]), size=int(artifact["size"]), media_type="text/x-diff",
            ))
        if "runtime_health" in requirements and not self._has_runtime_evidence(run_id):
            self._append_evidence(RuntimeEvidence(
                record_id=_record_id(), run_id=run_id, requirement_id="runtime_health",
                subject_digest=subject_digest, producer="muxdev.runtime",
                event_type="run_health", status="passed", details={},
            ))
        self._record_delivery_standard_evidence(run_id, subject_digest)
        records = self._records(run_id)
        decision = evaluate_gate(self._policy(run_id), records, subject_digest=subject_digest)
        current_run = self.store.get_run(run_id) or {}
        metadata = current_run.get("metadata")
        current_metadata = metadata if isinstance(metadata, dict) else {}
        defer_apply = bool(current_metadata.get("defer_apply"))
        paused = current_run.get("status") == "awaiting_approval"
        if decision.status == "PASS" and not paused and current_run.get("status") != "aborted" and defer_apply:
            self.store.append_event(run_id, "workspace.delivery_deferred", {
                "changeset_digest": canonical_hash(change_set.to_dict()),
                "reason": "conversation_candidate_requires_acceptance",
            })
        elif decision.status == "PASS" and not paused and current_run.get("status") != "aborted":
            try:
                applied = apply_change_set(change_set, worktree, self.workspace)
            except (WorkspaceConflictError, ValueError, OSError) as exc:
                self._runtime_failure(run_id, "changeset", str(exc))
            else:
                self.store.append_event(run_id, "workspace.applied", {
                    "files": applied,
                    "changeset_digest": canonical_hash(change_set.to_dict()),
                })
            records = self._records(run_id)
            decision = evaluate_gate(
                self._policy(run_id), records, subject_digest=subject_digest
            )
        status = self._final_status(current_run, decision.status)
        self.store.update_run(run_id, status=str(status), current_stage=None)
        chain_valid, chain_errors = self.store.verify_event_chain(run_id)
        if not chain_valid and decision.status == "PASS":
            self._runtime_failure(run_id, "finalize", "; ".join(chain_errors))
            records = self._records(run_id)
            decision = evaluate_gate(
                self._policy(run_id), records, subject_digest=subject_digest
            )
            status = RunStatus.BLOCKED
            self.store.update_run(run_id, status=str(status), current_stage=None)
            chain_valid, chain_errors = self.store.verify_event_chain(run_id)
        chain_events = self.store.events(run_id)
        chain_head = chain_events[-1] if chain_events else {}
        recovery = self._build_recovery_summary(
            run_id, current_run, records, decision, chain_events
        )
        report = self._evidence_report(
            run_id=run_id,
            route=route,
            subject_digest=subject_digest,
            artifact=artifact,
            changeset_artifact=changeset_artifact,
            records=records,
            decision=decision,
            chain_valid=chain_valid,
            chain_errors=chain_errors,
            chain_head=chain_head,
            snapshot=snapshot,
            change_set=change_set,
            recovery=recovery,
        )
        report_path = run_dir / "evidence-report.json"
        report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        self.store.add_artifact(
            run_id, name=report_path.name, path=report_path,
            kind="evidence_report", media_type="application/json",
        )
        main = str(route.get("main_provider") or "unknown")
        independent_valid = not any(
            getattr(item, "independent", True) is False
            for item in records if item.kind == "review"
        )
        self.store.record_outcome(run_id, main, {
            "success": status == RunStatus.COMPLETED, "gate_status": decision.status,
            "integrity_valid": chain_valid, "independent_valid": independent_valid,
            "cost_usd": cost_usd,
            "latency_seconds": round(time.monotonic() - started, 3),
        })
        return RunResult(run_id, status, run_dir, report_path)

    def _evidence_report(
        self, *, run_id, route, subject_digest, artifact, changeset_artifact,
        records, decision, chain_valid, chain_errors, chain_head, snapshot,
        change_set, recovery,
    ) -> EvidenceReport:
        return EvidenceReport(
            run_id=run_id,
            subject={
                "name": run_id, "digest": subject_digest,
                "artifacts": [
                    {"name": artifact["name"], "digest": artifact["digest"]},
                    {"name": changeset_artifact["name"], "digest": changeset_artifact["digest"]},
                ],
            },
            policy=self._policy(run_id), records=records, decision=decision,
            integrity={
                "valid": chain_valid, "event_chain_errors": chain_errors,
                "event_chain_sequence": chain_head.get("sequence", 0),
                "event_chain_head": chain_head.get("event_hash"),
                "records_hash": canonical_hash([
                    item.model_dump(mode="json") for item in records
                ]),
            },
            routing=dict(route),
            reviewer={
                "main": route.get("main_provider"),
                "reviewer": route.get("reviewer_provider"),
            },
            harness=build_harness_summary(self.store, run_id, snapshot, change_set),
            recovery=recovery,
        )

    @staticmethod
    def _final_status(run: Mapping[str, object], decision_status: str) -> RunStatus:
        if run.get("status") == "aborted":
            return RunStatus.ABORTED
        if run.get("status") == "awaiting_approval":
            return RunStatus.AWAITING_APPROVAL
        if decision_status == "PASS":
            return RunStatus.COMPLETED
        if decision_status == "WAITING_HUMAN":
            return RunStatus.AWAITING_APPROVAL
        return RunStatus.BLOCKED

    def _build_recovery_summary(self, run_id, run, records, decision, events):
        failed_stage = next(
            (
                item for item in reversed(self.store.stages(run_id))
                if item.get("status") == "failed" and item.get("provider")
            ),
            None,
        )
        fallback_provider = None
        if failed_stage:
            fallback_provider = self._fallback_provider(
                run_id, str(failed_stage.get("provider") or ""),
                role=str(failed_stage.get("role") or "") or None,
            )
        return build_recovery_summary(
            run_id, str(run.get("profile") or "standard"), records, decision, events,
            fallback_provider=fallback_provider,
        )

    def _pause_for_human(self, run_id: str, stage_id: str, profile: str, prompt: str) -> bool:
        if profile != "strict":
            return False
        existing = [
            item for item in self.store.interactions(run_id) if item["stage_id"] == stage_id
        ]
        if not existing:
            self.store.request_interaction(
                run_id, kind="approval", requirement_id="human_approval",
                prompt=prompt, stage_id=stage_id,
                details={
                    "question": prompt,
                    "options": [
                        {"id": "approve", "label": "批准并继续", "recommended": False},
                        {"id": "reject", "label": "拒绝并修订", "recommended": False},
                    ],
                    "allow_custom_input": True,
                    "blocking": True,
                    "risk": "high",
                    "timeout_seconds": 0,
                    "timeout_action": "wait",
                    "recommended_option_id": "approve",
                    "expires_at": None,
                    "reason": "可信交付门禁要求人工明确确认。",
                },
            )
            self._append_interaction_evidence(run_id, stage_id, "pending")
            return True
        status = str(existing[-1]["status"])
        self._replace_interaction_evidence(run_id, stage_id, status)
        if status == "rejected":
            self._runtime_failure(run_id, stage_id, "Required human approval was rejected.")
            return True
        return status != "approved"

    def _append_interaction_evidence(
        self, run_id: str, stage_id: str, decision: str
    ) -> None:
        self._append_evidence(InteractionEvidence(
            record_id=_record_id(), run_id=run_id, stage_id=stage_id,
            requirement_id="human_approval",
            subject_digest=self._subject_digest_from_run(run_id),
            producer="muxdev.runtime.interaction",
            interaction_type="approval" if decision != "rejected" else "rejection",
            decision=decision,
        ))

    def _replace_interaction_evidence(
        self, run_id: str, stage_id: str, status: str
    ) -> None:
        decision = {"approved": "approved", "rejected": "rejected"}.get(status, "pending")
        self._append_interaction_evidence(run_id, stage_id, decision)

    def _runtime_failure(
        self, run_id: str, stage_id: str, message: str, *,
        code: str | None = None, kind: str | None = None,
        errors: list[str] | None = None,
    ) -> None:
        self._append_evidence(RuntimeEvidence(
            record_id=_record_id(), run_id=run_id, stage_id=stage_id,
            requirement_id="runtime_health",
            subject_digest=self._subject_digest_from_run(run_id),
            producer="muxdev.runtime", event_type="policy_or_execution_failure",
            status="failed",
            details={
                "message": message,
                **({"code": code} if code else {}),
                **({"kind": kind} if kind else {}),
                **({"errors": errors[:20]} if errors else {}),
            },
        ))

    def _write_stage_artifact(
        self, run_id: str, stage_id: str, name: str, content: str, run_dir: Path
    ):
        safe = Path(name).name or f"{stage_id}.txt"
        path = run_dir / "artifacts" / stage_id / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self.store.add_artifact(
            run_id, name=safe, path=path, kind="stage_output",
            stage_id=stage_id, media_type="text/plain",
        )

    def _append_evidence(self, record: AnyEvidenceRecord) -> None:
        self.store.append_event(
            record.run_id, "evidence.record", record.model_dump(mode="json"),
            stage_id=record.stage_id, event_id=record.record_id,
        )

    def _records(self, run_id: str) -> list[AnyEvidenceRecord]:
        return [
            _evidence_from_payload(event["payload"])
            for event in self.store.events(run_id)
            if event["type"] == "evidence.record"
        ]

    def _has_runtime_evidence(self, run_id: str) -> bool:
        return any(
            item.kind == "runtime" and item.requirement_id == "runtime_health"
            for item in self._records(run_id)
        )

    def _record_delivery_standard_evidence(
        self,
        run_id: str,
        subject_digest: str,
    ) -> None:
        """Turn constrained review/check observations into per-rule Runtime facts."""
        snapshot = self._policy_snapshot(run_id)
        items = custom_items(snapshot.delivery_standard)
        if not items:
            return
        records = self._records(run_id)
        for item in items:
            standard_id = str(item["id"])
            verifier = item.get("verifier") if isinstance(item.get("verifier"), dict) else {}
            verifier_type = str(verifier.get("type") or "agent_review")
            if verifier_type in {"check", "runtime_check"}:
                status, message, record_ids = self._assess_check_standard(
                    records, standard_id, subject_digest
                )
            elif verifier_type == "artifact":
                status, message, record_ids = self._assess_artifact_standard(
                    records, subject_digest
                )
            elif verifier_type == "human_acceptance":
                status, message, record_ids = self._assess_human_standard(
                    records, subject_digest
                )
            else:
                status, message, record_ids = self._assess_review_standard(
                    records,
                    standard_id,
                    subject_digest,
                    security=str(item.get("stage_id") or "") == "security_review",
                    require_independent=snapshot.profile in {"standard", "strict"},
                )
            self._append_evidence(RuntimeEvidence(
                record_id=_record_id(),
                run_id=run_id,
                stage_id=str(item.get("stage_id") or "") or None,
                requirement_id=standard_requirement_id(standard_id),
                subject_digest=subject_digest,
                producer="muxdev.runtime.delivery-standard",
                event_type="delivery_standard_assessment",
                status=status,
                details={
                    "standard_id": standard_id,
                    "standard_text": str(item.get("completion") or item.get("text") or ""),
                    "deliverable": str(item.get("deliverable") or item.get("text") or ""),
                    "proof": str(item.get("proof") or item.get("text") or ""),
                    "message": message,
                    "source_record_ids": record_ids,
                },
            ))

    @staticmethod
    def _assess_artifact_standard(
        records: list[AnyEvidenceRecord],
        subject_digest: str,
    ) -> tuple[str, str, list[str]]:
        artifacts = [
            item for item in records
            if isinstance(item, ArtifactEvidence)
            and item.subject_digest == subject_digest
            and item.integrity_valid
            and str(item.digest).startswith("sha256:")
        ]
        if not artifacts:
            return "pending", "内容寻址产物尚未生成。", []
        artifact = artifacts[-1]
        return "passed", "内容寻址产物存在且完整性有效。", [artifact.record_id]

    @staticmethod
    def _assess_human_standard(
        records: list[AnyEvidenceRecord],
        subject_digest: str,
    ) -> tuple[str, str, list[str]]:
        approvals = [
            item for item in records
            if isinstance(item, InteractionEvidence)
            and item.subject_digest == subject_digest
            and item.interaction_type == "approval"
        ]
        if not approvals:
            return "pending", "尚未获得人工接受。", []
        approval = approvals[-1]
        if approval.decision != "approved" or not approval.integrity_valid:
            return "failed", "人工接受未批准或完整性无效。", [approval.record_id]
        return "passed", "人工接受已批准。", [approval.record_id]

    @staticmethod
    def _assess_check_standard(
        records: list[AnyEvidenceRecord],
        standard_id: str,
        subject_digest: str,
    ) -> tuple[str, str, list[str]]:
        checks = [
            item for item in records
            if isinstance(item, CheckEvidence)
            and standard_id in item.criteria_ids
            and item.subject_digest == subject_digest
        ]
        if not checks:
            return "pending", "绑定的运行检查尚未产生覆盖证据。", []
        check = checks[-1]
        if not check.integrity_valid or not check.reproducible:
            return "failed", "绑定检查的完整性或可复现性无效。", [check.record_id]
        if not check.passed:
            return "failed", "绑定的运行检查未通过。", [check.record_id]
        return "passed", "绑定的运行检查已通过并覆盖此标准。", [check.record_id]

    @staticmethod
    def _assess_review_standard(
        records: list[AnyEvidenceRecord],
        standard_id: str,
        subject_digest: str,
        *,
        security: bool,
        require_independent: bool,
    ) -> tuple[str, str, list[str]]:
        stage_id = "security_review" if security else "review"
        reviews = [
            item for item in records
            if isinstance(item, ReviewEvidence)
            and item.stage_id == stage_id
            and item.subject_digest == subject_digest
            and item.target_digest == subject_digest
        ]
        if not reviews:
            return "pending", "负责此标准的评审尚未完成。", []
        review = reviews[-1]
        if not review.integrity_valid:
            return "failed", "评审证据完整性无效。", [review.record_id]
        if require_independent and not review.independent:
            return "failed", "此 Profile 要求独立 Reviewer。", [review.record_id]
        assessment = next(
            (
                item for item in reversed(review.standard_assessments)
                if item.standard_id == standard_id
            ),
            None,
        )
        if assessment is None:
            return "pending", "Reviewer 未逐项确认此标准。", [review.record_id]
        if assessment.status != "satisfied":
            return "failed", assessment.note or "Reviewer 判定此标准未满足。", [review.record_id]
        if any(
            finding.severity == "high" and not finding.resolved
            for finding in review.findings
        ):
            return "failed", "评审仍有未解决的高风险问题。", [review.record_id]
        return "passed", assessment.note or "Reviewer 已确认此标准满足。", [review.record_id]

    def _has_failed_checks(self, run_id: str, worktree: Path) -> bool:
        subject = self._subject_digest(worktree)
        latest: dict[tuple[str | None, tuple[str, ...], str], CheckEvidence] = {}
        for item in self._records(run_id):
            if isinstance(item, CheckEvidence) and item.subject_digest == subject:
                latest[(item.stage_id, tuple(item.argv), item.cwd)] = item
        return any(item.exit_code != 0 for item in latest.values())

    def _policy(self, run_id: str):
        run = self.store.get_run(run_id) or {}
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        frozen = metadata.get("policy") if isinstance(metadata, dict) else None
        if isinstance(frozen, dict):
            policy = EvidencePolicy.model_validate(frozen)
            if policy.policy_hash != run.get("policy_hash"):
                raise ValueError("frozen EvidencePolicy does not match the run policy hash")
            return policy
        return load_evidence_policy(
            self.workspace, workflow=str(run["workflow"]), profile=str(run["profile"])
        )

    def _subject_digest(self, worktree: Path) -> str:
        return canonical_hash({"diff": diff_text(worktree)})

    def _subject_digest_from_run(self, run_id: str) -> str:
        run = self.store.get_run(run_id) or {}
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        return self._subject_digest(Path(str(metadata.get("worktree") or self.workspace)))


def _record_id() -> str:
    return f"rec_{uuid4().hex}"


def _evidence_from_payload(payload: Mapping[str, object]) -> AnyEvidenceRecord:
    classes = {
        "artifact": ArtifactEvidence,
        "check": CheckEvidence,
        "review": ReviewEvidence,
        "interaction": InteractionEvidence,
        "runtime": RuntimeEvidence,
    }
    return classes[str(payload["kind"])].model_validate(payload)


__all__ = ["RunFinalizationMixin", "RunResult"]
