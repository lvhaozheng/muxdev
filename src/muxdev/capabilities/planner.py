"""Policy-aware capability selection."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import CapabilityDescriptor
from .registry import CapabilityRegistry


@dataclass(frozen=True)
class CapabilityPlanner:
    registry: CapabilityRegistry

    def plan_for_role(self, role: str | None, *, allow_write: bool, allow_external: bool) -> list[CapabilityDescriptor]:
        planned: list[CapabilityDescriptor] = []
        for descriptor in self.registry.list_descriptors():
            if descriptor.roles and (role or "") not in descriptor.roles:
                continue
            if descriptor.side_effect == "write" and not allow_write:
                continue
            if descriptor.side_effect == "external" and not allow_external:
                continue
            planned.append(descriptor)
        return planned
