"""In-memory capability registry."""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import CapabilityDescriptor, CapabilityRuntime


@dataclass
class CapabilityRegistry:
    _descriptors: dict[str, CapabilityDescriptor] = field(default_factory=dict)
    _runtimes: dict[str, CapabilityRuntime] = field(default_factory=dict)

    def register(self, descriptor: CapabilityDescriptor, runtime: CapabilityRuntime | None = None) -> None:
        self._descriptors[descriptor.name] = descriptor
        if runtime:
            self._runtimes[descriptor.name] = runtime

    def list_descriptors(self) -> list[CapabilityDescriptor]:
        return list(self._descriptors.values())

    def get(self, name: str) -> CapabilityDescriptor:
        return self._descriptors[name]

    def runtime(self, name: str) -> CapabilityRuntime | None:
        return self._runtimes.get(name)
