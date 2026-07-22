"""Durable Run reservation before a Conversation enters an executing state."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..services.evidence_policy import load_evidence_policy
from ..workflows import load_workflow, validate_role_providers
from .delivery_standards import bind_standard_checks, extend_evidence_policy
from .run_setup import build_run_metadata, validate_run_options


_PROFILE_COST_LIMITS = {"lite": 0.25, "standard": 0.5, "strict": 2.0}


class RunReservationMixin:
    def _resolve_run_settings(
        self,
        workflow_name: str,
        profile: str,
        role_providers: Mapping[str, str],
        max_cost_usd: float,
    ) -> tuple[Any, Any, dict[str, str], float]:
        validate_run_options(workflow_name, profile)
        workflow = load_workflow(workflow_name)
        normalized = validate_role_providers(workflow, dict(role_providers))
        policy = load_evidence_policy(
            self.workspace, workflow=workflow_name, profile=profile
        )
        return policy, workflow, normalized, min(
            max_cost_usd, _PROFILE_COST_LIMITS[profile]
        )

    def reserve_run(
        self,
        task: str,
        *,
        run_id: str,
        provider: str,
        workflow_name: str,
        profile: str,
        role_providers: Mapping[str, str],
        max_cost_usd: float,
        delivery_context: Mapping[str, object],
        defer_apply: bool,
        worktree_path: Path,
    ) -> dict[str, object]:
        """Persist the Run identity before any background-only initialization."""
        validate_run_options(workflow_name, profile)
        if self.store.get_run(run_id):
            raise RuntimeError(f"run is already reserved: {run_id}")
        worktree = Path(worktree_path).resolve()
        if not worktree.is_dir():
            raise FileNotFoundError(f"conversation worktree not found: {worktree}")
        workflow = load_workflow(workflow_name)
        normalized_roles = validate_role_providers(workflow, dict(role_providers))
        policy = load_evidence_policy(
            self.workspace, workflow=workflow_name, profile=profile
        )
        delivery_standard = (
            delivery_context.get("delivery_standard")
            if isinstance(delivery_context.get("delivery_standard"), Mapping) else {}
        )
        policy = extend_evidence_policy(policy, delivery_standard)
        workflow = bind_standard_checks(workflow, delivery_standard)
        limit = min(max_cost_usd, _PROFILE_COST_LIMITS[profile])
        run_dir = self._run_dir(run_id)
        metadata = build_run_metadata(
            run_dir,
            worktree,
            limit,
            policy,
            workflow,
            normalized_roles,
            delivery_context,
            defer_apply,
        )
        metadata["reservation_state"] = "conversation_pending_start"
        run = self.store.create_run(
            run_id=run_id,
            task=task,
            workflow=workflow_name,
            profile=profile,
            provider=provider,
            policy_hash=policy.policy_hash,
            metadata=metadata,
        )
        self.store.append_event(
            run_id,
            "run.initializing",
            {"status": "initializing", "current_stage": "team_preflight"},
        )
        return run

    def _create_or_validate_run(
        self,
        reserved: Mapping[str, object] | None,
        *,
        run_id: str,
        task: str,
        workflow_name: str,
        profile: str,
        provider: str,
        policy: Any,
        workflow: Any,
        role_providers: Mapping[str, str],
        max_cost_usd: float,
        delivery_context: Mapping[str, object] | None,
        defer_apply: bool,
        run_dir: Path,
        worktree: Path,
    ) -> None:
        if reserved:
            self._validate_reserved_run(
                reserved,
                task=task,
                workflow_name=workflow_name,
                profile=profile,
                provider=provider,
                worktree=worktree,
                policy=policy,
                delivery_context=delivery_context,
            )
            return
        self.store.create_run(
            run_id=run_id,
            task=task,
            workflow=workflow_name,
            profile=profile,
            provider=provider,
            policy_hash=policy.policy_hash,
            metadata=build_run_metadata(
                run_dir,
                worktree,
                max_cost_usd,
                policy,
                workflow,
                role_providers,
                delivery_context,
                defer_apply,
            ),
        )

    @staticmethod
    def _validate_reserved_run(
        run: Mapping[str, object],
        *,
        task: str,
        workflow_name: str,
        profile: str,
        provider: str,
        worktree: Path,
        policy: Any,
        delivery_context: Mapping[str, object] | None,
    ) -> None:
        metadata = run.get("metadata") if isinstance(run.get("metadata"), Mapping) else {}
        expected = {
            "task": task,
            "workflow": workflow_name,
            "profile": profile,
            "provider": provider,
        }
        for field, value in expected.items():
            if str(run.get(field) or "") != str(value):
                raise RuntimeError(f"reserved Run {field} does not match the execution request")
        if run.get("status") != "created":
            raise RuntimeError("only a newly reserved Run can begin execution")
        if metadata.get("reservation_state") != "conversation_pending_start":
            raise RuntimeError("existing Run was not reserved for Conversation startup")
        if Path(str(metadata.get("worktree") or "")).resolve() != worktree.resolve():
            raise RuntimeError("reserved Run worktree does not match the execution request")
        if str(run.get("policy_hash") or "") != str(policy.policy_hash):
            raise RuntimeError("reserved Run delivery standard does not match the execution request")
        frozen_context = metadata.get("delivery_context")
        frozen_delivery_context = (
            dict(frozen_context) if isinstance(frozen_context, Mapping) else {}
        )
        if frozen_delivery_context != dict(delivery_context or {}):
            raise RuntimeError("reserved Run delivery context does not match the execution request")


__all__ = ["RunReservationMixin"]
