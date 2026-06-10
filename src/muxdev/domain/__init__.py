"""Contract-first domain layer for muxdev.

The objects exported here are deliberately free of runtime, daemon, storage,
or presentation imports. They provide stable payload shapes that higher layers
can adapt to while the legacy models module remains available.
"""

from .approvals import ApprovalRequest
from .events import DomainEvent
from .evidence import ArtifactDescriptor, EvidenceBundle
from .ids import new_run_id
from .memory import MemoryRef
from .provider_actions import ProviderActionRequest
from .run import AutomationDecision, PolicySpec, RunSpec, SkillRef
from .stage import StageExecutionInput, StageExecutionResult, UsageRecord

__all__ = [
    "ApprovalRequest",
    "ArtifactDescriptor",
    "AutomationDecision",
    "DomainEvent",
    "EvidenceBundle",
    "MemoryRef",
    "PolicySpec",
    "ProviderActionRequest",
    "RunSpec",
    "SkillRef",
    "StageExecutionInput",
    "StageExecutionResult",
    "UsageRecord",
    "new_run_id",
]
