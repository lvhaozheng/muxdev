"""Provider discovery and runtime-adapter public API."""

from .adapters import HeadlessCliProviderAdapter, MockProviderAdapter, ProviderAdapter, ProviderStageOutput, get_runtime_provider
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
    "ProviderAdapter",
    "ProviderDefinition",
    "ProviderProbe",
    "ProviderStageOutput",
    "ProviderStatus",
    "detect_providers",
    "get_provider_definition",
    "get_runtime_provider",
    "probe_provider",
]
