"""Signed delivery construction, atomic completion, and verification."""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from cryptography.hazmat.primitives import serialization

from .. import __version__
from ..core.canonical import canonical_json_bytes, canonical_sha256, sha256_bytes
from ..core.redaction import redact
from ..domain.attestation import (
    ATTESTATION_CONTRACT,
    CANONICAL_JSON_CONTRACT,
    AttestationVerification,
)
from ..models import RunStatus, utc_now
from ..storage.contracts import sha256_file
from .evidence import verify_run_evidence
from .trust import ProjectKey, ProjectSigningKeyStore, TrustError, verify_signature


class AttestationRequiredError(RuntimeError):
    """A high-risk Run cannot complete without a valid signed attestation."""


SAFE_ARTIFACTS = (
    "diff.patch",
    "attestation/final_report.sanitized.md",
    "contracts/delivery_contract.json",
    "validation/blind_validator_panel.json",
    "deliverables/test_report.md",
    "deliverables/review_report.md",
)


class DeliveryAttestationService:
    def __init__(
        self,
        board: Any,
        *,
        run_dir: Path,
        workspace: Path,
        muxdev_home: Path | None = None,
    ) -> None:
        self.board = board
        self.run_dir = Path(run_dir).expanduser().resolve()
        self.workspace = Path(workspace).expanduser().resolve()
        self.key_store = ProjectSigningKeyStore(self.workspace, muxdev_home=muxdev_home)

    def finalize(
        self,
        *,
        run_id: str,
        task: str,
        workflow: str,
        diff_path: Path,
        report_path: Path,
        repair: bool = False,
        inject_failure: str | None = None,
    ) -> dict[str, Any]:
        """Atomically commit the terminal State Event and its signed proof."""
        previous = self.board.latest_delivery_attestation(run_id)
        if previous and not repair:
            return previous
        run_spec_row, run_spec = self._run_spec(run_id)
        high_risk = str((run_spec.get("harness_policy") or {}).get("risk_level") or "normal") == "high"
        generation = int(previous.get("generation") or 0) + 1 if previous else 1
        attestation_id = f"att_{uuid4().hex}"
        previous_hash = str(previous.get("payload_hash") or "") or None if previous else None

        key: ProjectKey | None = None
        signing_error: str | None = None
        try:
            key = self.key_store.get_or_create(require_secure=high_risk)
            self.board.observe_signing_key(
                project_id=key.project_id,
                key_id=key.key_id,
                public_key_fingerprint=key.fingerprint,
                permission_status=key.permission_status,
                status="available",
            )
        except (TrustError, OSError, ValueError) as exc:
            signing_error = redact(str(exc))[:500]
            project_id = self.key_store.status().get("project_id") or "uninitialized"
            self.board.observe_signing_key(
                project_id=str(project_id), key_id=None, public_key_fingerprint=None,
                permission_status=str(self.key_store.status().get("permission_status") or "unavailable"),
                status="failed",
            )

        self._enter_attesting(run_id, repair=repair)
        safe_report = self._write_safe_report(
            run_id=run_id,
            workflow=workflow,
            report_path=report_path,
            attestation_id=attestation_id,
            generation=generation,
            signature_status="signed" if key else "unsigned",
            key_id=key.key_id if key else None,
        )
        evidence = verify_run_evidence(self.run_dir, run_id, self.board)
        approval_errors = self._validate_approvals(run_id, high_risk=high_risk)
        validation_errors = list(approval_errors)
        if not evidence.get("valid"):
            validation_errors.append("Evidence v2 verification failed")
        if high_risk and signing_error:
            validation_errors.append(signing_error)
        if high_risk and validation_errors:
            self._block_required(run_id, validation_errors)
            raise AttestationRequiredError("; ".join(validation_errors))

        try:
            with self.board.unit_of_work():
                completed_event = self.board.set_run_status(
                    run_id,
                    RunStatus.COMPLETED,
                    recovery_reason="delivery repair attestation" if repair else None,
                    idempotency_key=(f"run:completed:attestation:{generation}" if repair else "run:completed"),
                )
                if inject_failure == "after_completed_event":
                    raise RuntimeError("injected attestation failure after completed event")
                signature_status = "signed" if key else "unsigned"
                payload = self._build_payload(
                    run_id=run_id,
                    task=task,
                    workflow=workflow,
                    run_spec_row=run_spec_row,
                    run_spec=run_spec,
                    diff_path=Path(diff_path),
                    safe_report=safe_report,
                    attestation_id=attestation_id,
                    generation=generation,
                    previous_hash=previous_hash,
                    completed_event_hash=completed_event.event_hash,
                    signature_status=signature_status,
                    key=key,
                    evidence=evidence,
                )
                signature: dict[str, Any] | None = None
                if key is not None:
                    try:
                        signature = self._sign(key, canonical_json_bytes(payload))
                    except Exception as exc:
                        if high_risk:
                            raise AttestationRequiredError(redact(str(exc))) from exc
                        signing_error = redact(str(exc))[:500]
                        key = None
                        payload = {**payload, "signature_status": "unsigned", "key_id": None, "public_key_fingerprint": None}
                payload_hash = canonical_sha256(payload)
                if inject_failure == "before_attestation_insert":
                    raise RuntimeError("injected attestation failure before record insert")
                record = self.board.record_delivery_attestation(
                    attestation_id=attestation_id,
                    run_id=run_id,
                    generation=generation,
                    status="signed" if signature else "unsigned",
                    payload=payload,
                    payload_hash=payload_hash,
                    signature=signature,
                    key_id=str(signature.get("key_id")) if signature else None,
                    public_key_fingerprint=str(signature.get("public_key_fingerprint")) if signature else None,
                    evidence_valid=bool(evidence.get("valid")),
                    identity_status="self_asserted" if signature else "unsigned",
                    previous_attestation_hash=previous_hash,
                    error=signing_error,
                )
                if inject_failure == "after_attestation_insert":
                    raise RuntimeError("injected attestation failure after record insert")
        except AttestationRequiredError as exc:
            self._block_required(run_id, [str(exc)])
            raise
        except Exception:
            # Fault injection and unexpected database/signing failures prove that
            # completed and attestation never commit independently.
            if high_risk:
                self._block_required(run_id, ["signed completion transaction failed"])
            raise

        materialization_warning = self.materialize(record)
        if materialization_warning:
            record = {**record, "materialization_warning": materialization_warning}
        return record

    def materialize(self, record: Mapping[str, Any]) -> str | None:
        """Rebuild cache files from the authoritative database record."""
        try:
            target = self.run_dir / "attestation"
            target.mkdir(parents=True, exist_ok=True)
            _atomic_json(target / "attestation.json", dict(record.get("payload") or {}))
            signature = record.get("signature")
            if isinstance(signature, Mapping):
                _atomic_json(target / "signature.json", dict(signature))
                spki = base64.b64decode(str(signature.get("public_key") or ""), validate=True)
                public_key = serialization.load_der_public_key(spki)
                pem = public_key.public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                _atomic_bytes(target / "public-key.pem", pem)
            return None
        except (OSError, ValueError, TypeError) as exc:
            return redact(f"attestation cache materialization failed: {exc}")

    def _enter_attesting(self, run_id: str, *, repair: bool) -> None:
        run = self.board.get_run(run_id)
        if str(run.get("status")) == str(RunStatus.ATTESTING):
            return
        self.board.set_run_status(
            run_id,
            RunStatus.ATTESTING,
            recovery_reason="repairing completed delivery" if repair else None,
            idempotency_key=f"run:attesting:{'repair' if repair else 'initial'}",
        )

    def _block_required(self, run_id: str, errors: list[str]) -> None:
        message = redact("; ".join(errors))[:1000]
        try:
            self.board.add_error(run_id, None, "attestation_required", message)
            self.board.set_run_status(
                run_id,
                RunStatus.BLOCKED,
                recovery_reason="signed delivery attestation failed",
                idempotency_key=f"run:attestation-required:{canonical_sha256(errors)}",
            )
        except Exception:
            # Preserve the original signing/transaction exception.
            return

    def _run_spec(self, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        row = self.board.conn.execute("SELECT * FROM run_specs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return ({"payload_hash": None, "schema_version": 0, "legacy": 1}, {"schema_version": 0})
        result = dict(row)
        try:
            payload = json.loads(str(result.get("payload_json") or "{}"))
        except json.JSONDecodeError:
            payload = {}
        return result, payload if isinstance(payload, dict) else {}

    def _build_payload(
        self,
        *,
        run_id: str,
        task: str,
        workflow: str,
        run_spec_row: Mapping[str, Any],
        run_spec: Mapping[str, Any],
        diff_path: Path,
        safe_report: Path,
        attestation_id: str,
        generation: int,
        previous_hash: str | None,
        completed_event_hash: str,
        signature_status: str,
        key: ProjectKey | None,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        route = self.board.latest_route_decision(run_id, kind="main") or {}
        features = self.board.latest_task_feature_set(run_id) or {}
        reviews = self.board.list_review_assignments(run_id)
        current_review = reviews[-1] if reviews else None
        approvals = [self._approval_projection(row) for row in self.board.list_approvals(run_id=run_id)]
        attempts = [self._attempt_projection(row) for row in self.board.table_rows("provider_attempts", run_id=run_id)]
        tests = [self._test_projection(row) for row in self.board.table_rows("test_results", run_id=run_id)]
        harness_heads = self._harness_heads(run_id)
        execution_head = self._execution_head(run_id)
        baseline = dict(run_spec.get("repository_baseline") or {})
        end_commit = _git_text(self.workspace, "rev-parse", "HEAD") or None
        artifacts = self._artifact_projection(diff_path=diff_path, safe_report=safe_report)
        environment = self._environment_projection(attempts)
        delivery_mode = str((run_spec.get("routing_policy") or {}).get("delivery_mode") or "production")
        return {
            "contract_version": ATTESTATION_CONTRACT,
            "canonical_json": CANONICAL_JSON_CONTRACT,
            "attestation_id": attestation_id,
            "generation": generation,
            "previous_attestation_hash": previous_hash,
            "target_status": "completed",
            "signature_status": signature_status,
            "signed_at": utc_now(),
            "run_id": run_id,
            "task_sha256": sha256_bytes(task.encode("utf-8")),
            "run_spec": {
                "schema_version": int(run_spec_row.get("schema_version") or 0),
                "sha256": run_spec_row.get("payload_hash"),
                "history_complete": not bool(run_spec_row.get("legacy")) and bool(baseline.get("history_complete")),
            },
            "workflow": workflow,
            "risk": dict(run_spec.get("harness_policy") or {}),
            "delivery_mode": delivery_mode,
            "simulation": delivery_mode == "simulation",
            "project": {
                "project_id": key.project_id if key else self.key_store.status().get("project_id"),
                "vcs": baseline.get("vcs"),
                "start_commit": baseline.get("initial_commit"),
                "end_commit": end_commit,
                "baseline_dirty_hash": baseline.get("dirty_patch_hash"),
                "baseline_untracked_hash": baseline.get("untracked_manifest_hash"),
                "final_patch_hash": sha256_file(diff_path) if diff_path.is_file() else None,
            },
            "routing": {
                "route_decision_id": route.get("decision_id"),
                "route_decision_hash": route.get("decision_hash"),
                "feature_set_id": features.get("feature_set_id"),
                "feature_hash": route.get("feature_hash") or features.get("source_hash"),
                "policy_version": route.get("policy_version"),
                "scorer_version": route.get("scorer_version"),
                "main_provider": route.get("selected_main_provider"),
                "reviewer_provider": route.get("selected_reviewer_provider"),
                "benchmark_snapshot_id": route.get("benchmark_snapshot_id"),
                "benchmark_snapshot_hash": route.get("benchmark_snapshot_hash"),
            },
            "review": self._review_projection(current_review),
            "approvals": approvals,
            "provider_attempts": attempts,
            "harness_heads": harness_heads,
            "tests": tests,
            "evidence": {
                "valid": bool(evidence.get("valid")),
                "manifest_hash": _optional_file_hash(self.run_dir / "evidence" / "manifest.json"),
                "evaluation_hash": _optional_file_hash(self.run_dir / "evidence" / "evaluation.json"),
                "events_hash": _optional_file_hash(self.run_dir / "evidence" / "events.jsonl"),
                "head_hash": evidence.get("head_hash"),
            },
            "state_completed_event_hash": completed_event_hash,
            "execution_head_at_signing": execution_head,
            "environment": environment,
            "artifacts": artifacts,
            "key_id": key.key_id if key else None,
            "public_key_fingerprint": key.fingerprint if key else None,
        }

    def _validate_approvals(self, run_id: str, *, high_risk: bool) -> list[str]:
        errors: list[str] = []
        for row in self.board.list_approvals(run_id=run_id):
            try:
                subject = json.loads(str(row.get("subject_json") or "{}"))
            except json.JSONDecodeError:
                subject = {}
            expected = canonical_sha256(subject) if isinstance(subject, dict) else None
            # Historical subjects used the pre-v1 canonical helper. Its output
            # is byte-compatible unless Unicode normalization is material.
            if expected != row.get("subject_hash"):
                errors.append(f"approval subject hash drift: {row.get('approval_id')}")
            if high_risk:
                if not isinstance(subject, dict) or not subject.get("contract_version"):
                    errors.append(f"approval subject is untyped: {row.get('approval_id')}")
                if str(row.get("status")) != "approved":
                    errors.append(f"approval is not approved: {row.get('approval_id')}")
                if isinstance(subject, dict):
                    errors.extend(self._approval_anchor_errors(row, subject))
        return errors

    def _approval_anchor_errors(self, row: Mapping[str, Any], subject: Mapping[str, Any]) -> list[str]:
        approval_id = str(row.get("approval_id") or "")
        errors: list[str] = []
        if subject.get("run_id") != row.get("run_id"):
            errors.append(f"approval Run anchor drift: {approval_id}")
        contract = str(subject.get("contract_version") or "")
        approval_type = str(row.get("type") or "")
        if contract == "muxdev.approval_subject.v1":
            if subject.get("approval_type") != approval_type or subject.get("stage_id") != row.get("stage_id"):
                errors.append(f"approval type/stage anchor drift: {approval_id}")
            extra = subject.get("extra") if isinstance(subject.get("extra"), Mapping) else {}
            if approval_type in {"plan", "design"}:
                errors.extend(
                    self._planning_anchor_errors(
                        approval_id,
                        approval_type,
                        extra,
                        run_id=str(row.get("run_id") or ""),
                    )
                )
            elif approval_type == "merge":
                current_patch = _optional_file_hash(self.run_dir / "diff.patch")
                if extra.get("patch_hash") != current_patch:
                    errors.append(f"merge approval patch drift: {approval_id}")
            elif approval_type == "shell" and extra.get("command"):
                command = str(extra.get("command"))
                commands = {
                    str(item.get("command") or "")
                    for item in self.board.table_rows("test_results", run_id=str(row.get("run_id") or ""))
                }
                if not any(command in item for item in commands):
                    errors.append(f"shell approval command drift: {approval_id}")
        elif contract == "muxdev.heterogeneous-review-waiver.v1":
            route = self.board.latest_route_decision(str(row.get("run_id") or ""), kind="main") or {}
            if (
                subject.get("route_decision_id") != route.get("decision_id")
                or subject.get("decision_hash") != route.get("decision_hash")
                or subject.get("feature_hash") != route.get("feature_hash")
            ):
                errors.append(f"reviewer waiver route drift: {approval_id}")
            assignments = self.board.list_review_assignments(str(row.get("run_id") or ""))
            if not any(item.get("waiver_approval_id") == approval_id for item in assignments):
                errors.append(f"reviewer waiver is not referenced by current review: {approval_id}")
        elif contract == "muxdev.isolation_downgrade.v1":
            attempts = [
                item for item in self.board.table_rows("provider_attempts", run_id=str(row.get("run_id") or ""))
                if item.get("waiver_approval_id") == approval_id
            ]
            providers = {
                str(item.get("provider") or ""): item
                for item in subject.get("providers", [])
                if isinstance(item, Mapping)
            }
            if not attempts:
                errors.append(f"isolation waiver is not referenced by a Provider Attempt: {approval_id}")
            for attempt in attempts:
                bound = providers.get(str(attempt.get("provider") or ""))
                if not bound or (
                    bound.get("adapter_version") != attempt.get("adapter_version")
                    or bound.get("certification_id") != attempt.get("certification_id")
                    or bound.get("actual_isolation") != attempt.get("isolation_mode")
                ):
                    errors.append(f"isolation waiver Adapter/Attempt drift: {approval_id}")
                    break
        else:
            errors.append(f"unsupported approval subject version: {approval_id}")
        return errors

    def _planning_anchor_errors(
        self,
        approval_id: str,
        approval_type: str,
        extra: Mapping[str, Any],
        *,
        run_id: str,
    ) -> list[str]:
        errors: list[str] = []
        if approval_type == "design":
            contract = self.run_dir / "design" / "design_contract.json"
            if extra.get("design_contract_hash") != _optional_file_hash(contract):
                errors.append(f"design approval contract drift: {approval_id}")
            review_artifacts = [
                item for item in self.board.table_rows("artifacts", run_id=run_id)
                if item.get("stage_id") == "design_review" and item.get("kind") == "stage_output"
            ]
            current_review = _optional_file_hash(Path(str(review_artifacts[-1].get("path")))) if review_artifacts else None
            if extra.get("review_result_hash") != current_review:
                errors.append(f"design approval review drift: {approval_id}")
            revision_count = sum(
                1
                for item in self.board.table_rows("provider_attempts", run_id=run_id)
                if item.get("stage_id") == "design_revise"
                and item.get("status") not in {"retried", "provider_action", "feedback_requested"}
            )
            if int(extra.get("revision_count") or 0) != revision_count:
                errors.append(f"design approval revision drift: {approval_id}")
            return errors
        planning_stages = {
            "plan", "quick_plan", "scaffold_plan", "plan_revise", "design", "design_brief",
            "design_plan", "design_revise", "problem_statement", "requirements",
            "architecture_options", "system_design",
        }
        artifacts = [
            item for item in self.board.table_rows("artifacts", run_id=run_id)
            if item.get("stage_id") in planning_stages and item.get("kind") == "stage_output"
        ]
        current = _optional_file_hash(Path(str(artifacts[-1].get("path")))) if artifacts else None
        if extra.get("plan_hash") != current:
            errors.append(f"plan approval artifact drift: {approval_id}")
        return errors

    @staticmethod
    def _approval_projection(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "approval_id": row.get("approval_id"),
            "stage_id": row.get("stage_id"),
            "type": row.get("type"),
            "status": row.get("status"),
            "subject_version": row.get("subject_version"),
            "subject_hash": row.get("subject_hash"),
            "decided_at": row.get("decided_at"),
            "operator_source": row.get("operator_source"),
        }

    @staticmethod
    def _attempt_projection(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "stage_id": row.get("stage_id"), "provider": row.get("provider"),
            "attempt": row.get("attempt"), "status": row.get("status"),
            "returncode": row.get("returncode"), "failure_kind": row.get("failure_kind"),
            "adapter_version": row.get("adapter_version"), "certification_id": row.get("certification_id"),
            "trust_tier": row.get("trust_tier"), "isolation_mode": row.get("isolation_mode"),
            "waiver_approval_id": row.get("waiver_approval_id"),
        }

    @staticmethod
    def _test_projection(row: Mapping[str, Any]) -> dict[str, Any]:
        command = str(row.get("command") or "")
        summary = str(row.get("summary") or "")
        return {
            "stage_id": row.get("stage_id"), "passed": bool(row.get("passed")),
            "command_hash": sha256_bytes(command.encode("utf-8")),
            "result_hash": sha256_bytes(summary.encode("utf-8")),
            "source": "muxdev.test_result",
        }

    @staticmethod
    def _review_projection(review: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not review:
            return None
        return {
            "review_id": review.get("review_id"), "attempt": review.get("attempt"),
            "status": review.get("status"), "snapshot_hash": review.get("snapshot_hash"),
            "verdict": review.get("verdict"), "waiver_approval_id": review.get("waiver_approval_id"),
            "route_decision_id": review.get("route_decision_id"),
        }

    def _harness_heads(self, run_id: str) -> list[dict[str, Any]]:
        heads: dict[tuple[str, str, int], Any] = {}
        for event in self.board.list_harness_events(run_id):
            heads[(event.stage_id, event.provider, event.attempt)] = event
        return [
            {
                "stage_id": stage, "provider": provider, "attempt": attempt,
                "sequence": event.sequence, "event_hash": event.event_hash,
            }
            for (stage, provider, attempt), event in sorted(heads.items())
        ]

    def _execution_head(self, run_id: str) -> dict[str, Any] | None:
        row = self.board.conn.execute(
            "SELECT * FROM execution_events WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        projection = {
            "sequence": payload.get("sequence"), "event_type": payload.get("event_type"),
            "event_id": payload.get("event_id"), "created_at_ms": payload.get("created_at_ms"),
        }
        return {**projection, "derived_hash": canonical_sha256(projection)}

    def _artifact_projection(self, *, diff_path: Path, safe_report: Path) -> list[dict[str, Any]]:
        candidates = [diff_path, safe_report]
        candidates.extend(self.run_dir / item for item in SAFE_ARTIFACTS[2:])
        result: list[dict[str, Any]] = []
        for path in candidates:
            resolved = path.expanduser().resolve()
            try:
                relative = resolved.relative_to(self.run_dir).as_posix()
            except ValueError:
                continue
            if not resolved.is_file() or resolved.is_symlink():
                continue
            result.append(
                {
                    "relative_path": relative,
                    "size": resolved.stat().st_size,
                    "media_type": _media_type(resolved),
                    "sha256": sha256_file(resolved),
                }
            )
        return sorted(result, key=lambda item: str(item["relative_path"]))

    @staticmethod
    def _environment_projection(attempts: list[dict[str, Any]]) -> dict[str, Any]:
        providers = sorted(
            {
                (str(item.get("provider") or "unknown"), str(item.get("adapter_version") or "unknown"))
                for item in attempts
            }
        )
        return {
            "os": platform.system(), "architecture": platform.machine(),
            "python": platform.python_version(), "muxdev": __version__,
            "git": _git_version(),
            "providers": [{"provider": provider, "adapter_version": version} for provider, version in providers],
            "attestation_policy": "muxdev.attestation-policy.v1",
        }

    def _write_safe_report(
        self,
        *,
        run_id: str,
        workflow: str,
        report_path: Path,
        attestation_id: str,
        generation: int,
        signature_status: str,
        key_id: str | None,
    ) -> Path:
        source_hash = _optional_file_hash(report_path)
        lines = [
            f"# muxdev signed delivery summary: {run_id}", "",
            f"- Workflow: {workflow}",
            f"- Attestation: {attestation_id}",
            f"- Generation: {generation}",
            f"- Signature status: {signature_status}",
            f"- Key ID: {key_id or '-'}",
            f"- Full local report hash: {source_hash or '-'}", "",
            "This privacy-minimal projection excludes task text, absolute paths, prompts, transcripts, and provider raw output.",
        ]
        path = self.run_dir / "attestation" / "final_report.sanitized.md"
        _atomic_bytes(path, ("\n".join(lines) + "\n").encode("utf-8"))
        return path

    @staticmethod
    def _sign(key: ProjectKey, payload: bytes) -> dict[str, Any]:
        signature = key.private_key.sign(payload)
        return {
            "contract_version": "muxdev.ed25519-signature.v1",
            "algorithm": "Ed25519",
            "key_id": key.key_id,
            "project_id": key.project_id,
            "public_key_format": "SubjectPublicKeyInfo/DER",
            "public_key": base64.b64encode(key.public_key_spki).decode("ascii"),
            "public_key_fingerprint": key.fingerprint,
            "payload_sha256": sha256_bytes(payload),
            "signature": base64.b64encode(signature).decode("ascii"),
            "created_at": utc_now(),
        }


def verify_attestation_record(
    payload: Mapping[str, Any],
    signature: Mapping[str, Any] | None,
    *,
    evidence_valid: bool = True,
    trusted_fingerprint: str | None = None,
    require_trusted: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    signature_status = str(payload.get("signature_status") or "unsigned")
    integrity_valid = False
    identity_status = "unsigned"
    trusted = False
    if signature_status == "unsigned" or not signature:
        warnings.append("delivery is unsigned")
    else:
        integrity_valid, error = verify_signature(canonical_json_bytes(payload), dict(signature))
        if error:
            errors.append(error)
        fingerprint = str(signature.get("public_key_fingerprint") or "")
        if trusted_fingerprint:
            if fingerprint == _normalize_fingerprint(trusted_fingerprint):
                identity_status = "trusted" if integrity_valid else "mismatch"
                trusted = integrity_valid
            else:
                identity_status = "mismatch"
                errors.append("trusted public key fingerprint mismatch")
        else:
            identity_status = "self_asserted" if integrity_valid else "mismatch"
            if integrity_valid:
                warnings.append("signature integrity is valid but signer identity is self-asserted")
    if not evidence_valid:
        errors.append("Evidence v2 verification failed")
    if require_trusted and not trusted:
        errors.append("trusted signer identity is required")
    valid = integrity_valid and evidence_valid and identity_status != "mismatch" and (trusted if require_trusted else True)
    result = AttestationVerification(
        integrity_valid=integrity_valid,
        evidence_valid=evidence_valid,
        identity_status=identity_status,
        trusted=trusted,
        valid=valid,
        warnings=tuple(warnings),
        errors=tuple(errors),
        run_id=str(payload.get("run_id") or "") or None,
        attestation_id=str(payload.get("attestation_id") or "") or None,
        payload_hash=canonical_sha256(payload),
    )
    return result.to_dict()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_bytes(path, json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _optional_file_hash(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def _git_text(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _git_version() -> str | None:
    try:
        completed = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = completed.stdout.strip()
    match = re.search(r"\d+(?:\.\d+)+", value)
    return match.group(0) if match else None


def _media_type(path: Path) -> str:
    return {
        ".json": "application/json", ".jsonl": "application/x-ndjson",
        ".md": "text/markdown", ".patch": "text/x-diff", ".txt": "text/plain",
    }.get(path.suffix.lower(), "application/octet-stream")


def _normalize_fingerprint(value: str) -> str:
    value = value.strip().lower()
    return value if value.startswith("sha256:") else "sha256:" + value
