"""Provider discovery and runtime-adapter public API."""

from .adapters import HeadlessCliProviderAdapter, MockProviderAdapter, ProviderAdapter, ProviderStageOutput, get_runtime_provider
from .contracts import (
    ProviderActionDecision,
    ProviderCapabilities,
    ProviderDescriptor,
    ProviderResumeResult,
    ProviderRuntime,
    ProviderRuntimeKind,
    ProviderSession,
    ProviderStreamEvent,
)
from .planner import ProviderPlanner, ProviderRouteDecision
from .registry import (
    CapabilityState,
    CommandResult,
    ProviderDefinition,
    ProviderProbe,
    ProviderStatus,
    detect_providers,
    get_provider_definition,
    probe_provider,
)

__all__ = [
    "CapabilityState",
    "CommandResult",
    "HeadlessCliProviderAdapter",
    "MockProviderAdapter",
    "ProviderActionDecision",
    "ProviderAdapter",
    "ProviderCapabilities",
    "ProviderDefinition",
    "ProviderDescriptor",
    "ProviderProbe",
    "ProviderPlanner",
    "ProviderResumeResult",
    "ProviderRouteDecision",
    "ProviderRuntime",
    "ProviderRuntimeKind",
    "ProviderSession",
    "ProviderStageOutput",
    "ProviderStatus",
    "ProviderStreamEvent",
    "detect_providers",
    "get_provider_definition",
    "get_runtime_provider",
    "probe_provider",
]
