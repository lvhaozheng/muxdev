"""Structured contracts and evidence bundles for trusted delivery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..models import utc_now


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def canonical_hash(payload: dict[str, Any]) -> str:
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def write_json_artifact(path: Path, payload: dict[str, Any]) -> tuple[Path, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, sha256_file(path)


def write_stage_contract(
    run_dir: Path,
    *,
    run_id: str,
    stage_id: str,
    role: str | None,
    provider: str,
    task_hash: str,
    workflow_hash: str,
    pre_patch_hash: str,
) -> tuple[Path, str, dict[str, Any]]:
    payload = {
        "contract_version": "muxdev.stage_contract.v1",
        "run_id": run_id,
        "stage_id": stage_id,
        "role": role,
        "provider": provider,
        "task_hash": task_hash,
        "workflow_hash": workflow_hash,
        "pre_patch_hash": pre_patch_hash,
        "created_at": utc_now(),
    }
    path, digest = write_json_artifact(run_dir / "contracts" / f"{stage_id}.stage_contract.json", payload)
    payload["contract_hash"] = digest
    path, digest = write_json_artifact(path, payload)
    return path, digest, payload


def write_evidence_bundle(
    run_dir: Path,
    *,
    run_id: str,
    stage_id: str | None,
    artifacts: list[dict[str, Any]],
    patch_hash: str,
    snapshot_ref: str | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    payload = {
        "contract_version": "muxdev.evidence_bundle.v1",
        "run_id": run_id,
        "stage_id": stage_id,
        "artifacts": artifacts,
        "patch_hash": patch_hash,
        "snapshot_ref": snapshot_ref,
        "created_at": utc_now(),
    }
    name = stage_id or "run"
    path, digest = write_json_artifact(run_dir / "evidence" / f"{name}.evidence.json", payload)
    payload["evidence_hash"] = digest
    path, digest = write_json_artifact(path, payload)
    return path, digest, payload


def write_role_result_contract(
    run_dir: Path,
    *,
    run_id: str,
    stage_id: str,
    role: str | None,
    provider: str,
    decision: str,
    summary: str,
    findings: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    evidence_hash: str,
    patch_hash: str,
) -> tuple[Path, str, dict[str, Any]]:
    payload = {
        "contract_version": "muxdev.role_result.v1",
        "run_id": run_id,
        "stage_id": stage_id,
        "role": role,
        "provider": provider,
        "decision": decision,
        "summary": summary,
        "findings": findings,
        "evidence": evidence,
        "evidence_hash": evidence_hash,
        "patch_hash": patch_hash,
        "created_at": utc_now(),
    }
    path, digest = write_json_artifact(run_dir / "contracts" / f"{stage_id}.role_result.json", payload)
    payload["role_result_hash"] = digest
    path, digest = write_json_artifact(path, payload)
    return path, digest, payload


def write_blind_validator_panel(
    run_dir: Path,
    *,
    run_id: str,
    task_hash: str,
    patch_hash: str,
    test_results: list[dict[str, Any]],
    review_blockers: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> tuple[Path, str, dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if any(not bool(row.get("passed")) for row in test_results):
        findings.append({"severity": "high", "type": "test_failure", "summary": "one or more tests failed"})
    for blocker in review_blockers:
        findings.append(
            {
                "severity": blocker.get("severity", "medium"),
                "type": blocker.get("type", "review_blocker"),
                "file": blocker.get("file"),
                "line": blocker.get("line"),
                "summary": blocker.get("suggestion", ""),
            }
        )
    for error in errors:
        findings.append({"severity": "high", "type": error.get("type", "error"), "summary": error.get("message", "")})
    decision = "reject" if findings else "accept"
    payload = {
        "contract_version": "muxdev.blind_validator_panel.v1",
        "run_id": run_id,
        "validator": "local-blind-validator",
        "decision": decision,
        "task_hash": task_hash,
        "patch_hash": patch_hash,
        "minimal_context": ["task_hash", "patch_hash", "test_results", "review_blockers", "errors"],
        "findings": findings,
    }
    payload["validator_hash"] = canonical_hash(payload)
    payload["created_at"] = utc_now()
    path, digest = write_json_artifact(run_dir / "validation" / "blind_validator_panel.json", payload)
    return path, digest, payload


def artifact_descriptor(path: Path, *, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": str(path),
        "sha256": sha256_file(path) if path.exists() and path.is_file() else None,
    }
