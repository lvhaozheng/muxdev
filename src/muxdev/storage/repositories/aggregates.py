"""Business-focused repository views over the v7 Blackboard.

These facades deliberately keep the existing database and artifact layout.
They expose only operations belonging to one aggregate, which lets application
code depend on a narrow port while legacy/runtime code migrates incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from .provider_actions import ProviderActionsRepository


@dataclass(frozen=True)
class _RepositoryView:
    blackboard: Any
    operations: ClassVar[frozenset[str]] = frozenset()

    def __getattr__(self, name: str) -> Any:
        if name not in self.operations:
            raise AttributeError(f"{type(self).__name__} does not expose {name!r}")
        return getattr(self.blackboard, name)


class LifecycleRepository(_RepositoryView):
    operations = frozenset(
        {
            "create_run",
            "set_run_status",
            "get_run",
            "list_runs",
            "upsert_stage",
            "upsert_agent",
            "reset_stage",
            "skip_stage",
            "fail_worker",
            "record_recovery_fork",
            "add_checkpoint",
            "add_error",
            "list_state_events",
            "replay_state",
            "replay_run",
        }
    )


class InteractionRepository(_RepositoryView):
    operations = frozenset(
        {
            "create_approval",
            "find_approval",
            "decide_approval",
            "list_approvals",
            "create_provider_action",
            "list_provider_actions",
            "update_provider_action_status",
            "respond_provider_action",
            "add_feedback_event",
            "list_feedback_events",
            "update_feedback_event_status",
        }
    )


class RoutingTrustRepository(_RepositoryView):
    operations = frozenset(
        {
            "record_task_feature_set",
            "latest_task_feature_set",
            "get_route_decision",
            "latest_route_decision",
            "list_route_decisions",
            "record_route_decision",
            "route_candidate_from_payload",
            "route_decision_from_row",
            "record_routing_outcome",
            "routing_outcomes",
            "record_review_assignment",
            "update_review_assignment",
            "get_review_assignment",
            "list_review_assignments",
            "record_delivery_attestation",
            "latest_delivery_attestation",
            "get_delivery_attestation",
            "list_delivery_attestations",
            "observe_signing_key",
            "record_attestation_export",
            "get_attestation_export",
        }
    )


class EvidenceRepository(_RepositoryView):
    operations = frozenset(
        {
            "add_artifact",
            "add_stage_contract",
            "replace_evidence_v2",
            "add_ledger_event",
            "add_snapshot",
            "add_validator_panel",
            "add_test_result",
            "add_review_blocker",
        }
    )


class BenchmarkRepository(_RepositoryView):
    operations = frozenset(
        {
            "register_benchmark_snapshot",
            "latest_benchmark_snapshot",
            "list_benchmark_snapshots",
            "create_benchmark_execution",
            "update_benchmark_execution",
            "get_benchmark_execution",
            "list_benchmark_executions",
            "append_benchmark_event",
            "list_benchmark_events",
            "record_benchmark_case_result",
            "list_benchmark_case_results",
            "record_benchmark_report",
            "get_benchmark_report",
        }
    )


class EcosystemRepository(_RepositoryView):
    operations = frozenset(
        {
            "record_adapter_certification",
            "get_adapter_certification",
            "list_adapter_certifications",
            "append_harness_events",
            "list_harness_events",
            "record_harness_cancellation",
            "start_provider_attempt",
            "complete_provider_attempt",
            "add_session_capsule",
            "add_ci_rescue",
            "update_ci_rescue",
            "add_cache_entry",
            "upsert_skill_lock",
            "add_guardrail_event",
            "add_parallel_conflict",
            "list_parallel_conflicts",
            "add_semantic_merge_review",
            "list_semantic_merge_reviews",
            "upsert_provider_learning",
            "list_provider_learning",
            "add_multi_repo_orchestration",
            "list_multi_repo_orchestrations",
            "add_usage",
            "usage_total_cost",
        }
    )


class TransactionsRepository(_RepositoryView):
    operations = frozenset({"unit_of_work", "storage_health"})


@dataclass(frozen=True)
class BlackboardRepositories:
    """Named repository set sharing one Blackboard transaction boundary."""

    lifecycle: LifecycleRepository
    interaction: InteractionRepository
    routing_trust: RoutingTrustRepository
    evidence: EvidenceRepository
    benchmark: BenchmarkRepository
    ecosystem: EcosystemRepository
    transactions: TransactionsRepository
    provider_actions: ProviderActionsRepository

    @classmethod
    def from_blackboard(cls, blackboard: Any) -> "BlackboardRepositories":
        return cls(
            lifecycle=LifecycleRepository(blackboard),
            interaction=InteractionRepository(blackboard),
            routing_trust=RoutingTrustRepository(blackboard),
            evidence=EvidenceRepository(blackboard),
            benchmark=BenchmarkRepository(blackboard),
            ecosystem=EcosystemRepository(blackboard),
            transactions=TransactionsRepository(blackboard),
            provider_actions=ProviderActionsRepository(blackboard),
        )
