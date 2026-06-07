"""Role session handoff capsule artifacts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..core.platforms import hidden_subprocess_kwargs
from ..core.redaction import redact
from ..models import utc_now
from ..storage.contracts import canonical_hash


def write_session_capsule(
    run_dir: Path,
    *,
    run_id: str,
    stage_id: str,
    role: str | None,
    provider: str,
    worktree: Path,
    kind: str,
    summary: str,
    patch_text: str,
    patch_hash: str,
    snapshot_ref: str | None = None,
    artifact_path: Path | None = None,
    memory_refs: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    open_findings: list[dict[str, Any]] | None = None,
    provider_actions: list[str] | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """Write a compact state capsule that can be used to attach or hand off."""
    capsule_dir = run_dir / "capsules"
    capsule_dir.mkdir(parents=True, exist_ok=True)
    patch_path = capsule_dir / f"{stage_id}.handoff.patch"
    patch_path.write_text(redact(patch_text), encoding="utf-8")
    payload: dict[str, Any] = {
        "contract_version": "muxdev.session_capsule.v1",
        "run_id": run_id,
        "stage_id": stage_id,
        "role": role,
        "provider": provider,
        "kind": kind,
        "status": "handoff_ready",
        "summary": redact(summary),
        "worktree": str(worktree),
        "worktree_head": _git_stdout(worktree, "rev-parse", "HEAD"),
        "snapshot_ref": snapshot_ref,
        "artifact_path": str(artifact_path) if artifact_path else None,
        "latest_diff_path": str(patch_path),
        "patch_hash": patch_hash,
        "memory_refs": memory_refs or [],
        "evidence_refs": evidence_refs or [],
        "open_findings": open_findings or [],
        "provider_actions": provider_actions or [],
        "created_at": utc_now(),
    }
    digest = canonical_hash(payload)
    payload["capsule_hash"] = digest
    path = capsule_dir / f"{stage_id}.session_capsule.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, digest, payload


def _git_stdout(worktree: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ["git", *args],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    return value or None
