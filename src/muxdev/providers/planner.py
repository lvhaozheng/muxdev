"""Provider routing policy for workflow stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping


Recommender = Callable[[str | None, str], tuple[str, dict[str, object]]]


@dataclass(frozen=True)
class ProviderRouteDecision:
    provider: str
    reason: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def trace_payload(self) -> dict[str, object]:
        return {"provider": self.provider, "reason": self.reason, **dict(self.metadata)}


@dataclass(frozen=True)
class ProviderPlanner:
    role_providers: Mapping[str, str] = field(default_factory=dict)
    recommender: Recommender | None = None

    def select(self, *, role: str | None, fallback: str) -> ProviderRouteDecision:
        explicit = self.role_providers.get(role or "")
        if explicit:
            return ProviderRouteDecision(explicit, "explicit role override", {"role": role})
        if self.recommender:
            selected, decision = self.recommender(role, fallback)
            return ProviderRouteDecision(selected, "provider learning recommendation", {"role": role, "decision": decision})
        return ProviderRouteDecision(fallback, "fallback provider", {"role": role})
