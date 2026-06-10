"""Contracts for runtime-callable capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol


SideEffect = Literal["none", "read", "write", "external"]


@dataclass(frozen=True)
class CapabilityDescriptor:
    name: str
    owner: Literal["core", "plugin", "provider"]
    side_effect: SideEffect
    roles: frozenset[str] = frozenset()
    required_approval: str | None = None
    sandbox_required: bool = False


@dataclass(frozen=True)
class CapabilityCall:
    name: str
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityResult:
    ok: bool
    payload: Mapping[str, object] = field(default_factory=dict)
    error: str | None = None


class CapabilityRuntime(Protocol):
    descriptor: CapabilityDescriptor

    def execute(self, request: CapabilityCall, ctx: object) -> CapabilityResult:
        ...
