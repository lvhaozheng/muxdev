"""Small Run setup helpers kept outside the execution engine."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..models import WorkflowDefinition
from ..models.evidence import EvidencePolicy
from .workspace import WorkspaceSnapshot, snapshot_workspace
from .worktree import WorktreeManager


def validate_run_options(workflow_name: str, profile: str) -> None:
    if workflow_name not in {"change", "design", "review", "test"}:
        raise ValueError("workflow must be change, design, review, or test")
    if profile not in {"lite", "standard", "strict"}:
        raise ValueError("profile must be lite, standard, or strict")


def prepare_run_subject(
    workspace: Path,
    run_id: str,
    run_dir: Path,
    *,
    worktree_path: Path | None,
    base_workspace_manifest: Mapping[str, object] | None,
) -> tuple[WorkspaceSnapshot, Path]:
    snapshot = (
        WorkspaceSnapshot.from_dict(dict(base_workspace_manifest))
        if base_workspace_manifest
        else snapshot_workspace(workspace)
    )
    if worktree_path is None:
        return snapshot, WorktreeManager(workspace).prepare(run_id, run_dir).path
    worktree = Path(worktree_path).resolve()
    if not worktree.is_dir():
        raise FileNotFoundError(f"conversation worktree not found: {worktree}")
    return snapshot, worktree


def build_run_metadata(
    run_dir: Path,
    worktree: Path,
    max_cost_usd: float,
    policy: EvidencePolicy,
    workflow: WorkflowDefinition,
    role_providers: Mapping[str, str],
    delivery_context: Mapping[str, object] | None,
    defer_apply: bool,
) -> dict[str, object]:
    return {
        "run_dir": str(run_dir),
        "worktree": str(worktree),
        "max_cost_usd": max_cost_usd,
        "policy": policy.model_dump(mode="json"),
        "workflow_definition": workflow.model_dump(mode="json"),
        "role_providers": dict(role_providers),
        "delivery_context": dict(delivery_context or {}),
        "defer_apply": bool(defer_apply),
    }


def resumable_session_id(
    run_metadata: Mapping[str, object],
    *,
    stage_role: str | None,
    provider: str,
    can_write: bool,
) -> str:
    """Return only the implementer session that may safely enter a write stage."""
    delivery = run_metadata.get("delivery_context")
    delivery_context = delivery if isinstance(delivery, Mapping) else {}
    session = delivery_context.get("implementer_session")
    implementer_session = session if isinstance(session, Mapping) else {}
    if stage_role != "code" or not can_write:
        return ""
    if str(implementer_session.get("provider") or "") != provider:
        return ""
    return str(implementer_session.get("session_id") or "")


__all__ = [
    "build_run_metadata",
    "prepare_run_subject",
    "resumable_session_id",
    "validate_run_options",
]
