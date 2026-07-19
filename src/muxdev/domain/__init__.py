"""Contract-first domain layer for muxdev.

The objects exported here are deliberately free of runtime, daemon, storage,
or presentation imports. They provide stable payload shapes that higher layers
can adapt to while the legacy models module remains available.
"""

from .approvals import ApprovalRequest
from .events import DomainEvent
from .evidence import ArtifactDescriptor, EvidenceBundle
from .execution import (
    ACTIVE_EXECUTION_STATES,
    TERMINAL_EXECUTION_STATES,
    CancellationToken,
    ExecutionGuard,
    ExecutionLease,
    ExecutionState,
    LeaseLost,
    ReconciliationRequired,
    RetryableExecutionError,
    TaskCancelled,
)
from .ids import new_run_id
from .memory import MemoryRef
from .provider_actions import ProviderActionRequest
from .routing import ReviewAssignment, RouteCandidate, RouteDecision, TaskFeatureSet, verify_route_decision_hash
from .run import AutomationDecision, HarnessPolicySpec, PolicySpec, RepositoryBaseline, RoutingPolicySpec, RunSpec, SkillRef
from .stage import StageExecutionInput, StageExecutionResult, UsageRecord
from .state_events import (
    InvalidStateTransition,
    StateEventEnvelope,
    UnknownStateEvent,
    initial_run_state,
    reduce_run_state,
    verify_event_hash,
)
from .attestation import (
    ATTESTATION_CONTRACT,
    BUNDLE_CONTRACT,
    CANONICAL_JSON_CONTRACT,
    SIGNATURE_CONTRACT,
    AttestationVerification,
    DeliveryAttestation,
    PreparedSignature,
)

__all__ = [
    "ATTESTATION_CONTRACT",
    "ApprovalRequest",
    "ArtifactDescriptor",
    "AttestationVerification",
    "AutomationDecision",
    "BUNDLE_CONTRACT",
    "CANONICAL_JSON_CONTRACT",
    "DeliveryAttestation",
    "DomainEvent",
    "EvidenceBundle",
    "ExecutionGuard",
    "ExecutionLease",
    "ExecutionState",
    "CancellationToken",
    "LeaseLost",
    "ReconciliationRequired",
    "RetryableExecutionError",
    "TaskCancelled",
    "ACTIVE_EXECUTION_STATES",
    "TERMINAL_EXECUTION_STATES",
    "MemoryRef",
    "InvalidStateTransition",
    "HarnessPolicySpec",
    "RoutingPolicySpec",
    "PolicySpec",
    "ProviderActionRequest",
    "PreparedSignature",
    "RepositoryBaseline",
    "RunSpec",
    "ReviewAssignment",
    "RouteCandidate",
    "RouteDecision",
    "TaskFeatureSet",
    "verify_route_decision_hash",
    "StateEventEnvelope",
    "SkillRef",
    "SIGNATURE_CONTRACT",
    "StageExecutionInput",
    "StageExecutionResult",
    "UsageRecord",
    "UnknownStateEvent",
    "initial_run_state",
    "new_run_id",
    "reduce_run_state",
    "verify_event_hash",
]
