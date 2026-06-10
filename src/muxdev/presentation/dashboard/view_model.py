"""Dashboard view model shared by HTML, API, and future TUI renderers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class DashboardViewModel:
    run: Mapping[str, Any]
    stages: tuple[Mapping[str, Any], ...] = ()
    approvals: tuple[Mapping[str, Any], ...] = ()
    provider_actions: tuple[Mapping[str, Any], ...] = ()
    artifacts: tuple[Mapping[str, Any], ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_read_model(cls, payload: Mapping[str, Any]) -> "DashboardViewModel":
        return cls(
            run=payload.get("run", {}) if isinstance(payload.get("run"), Mapping) else {},
            stages=tuple(item for item in payload.get("stages", []) if isinstance(item, Mapping)),
            approvals=tuple(item for item in payload.get("approvals", []) if isinstance(item, Mapping)),
            provider_actions=tuple(item for item in payload.get("provider_actions", []) if isinstance(item, Mapping)),
            artifacts=tuple(item for item in payload.get("artifacts", []) if isinstance(item, Mapping)),
            summary=payload.get("summary", {}) if isinstance(payload.get("summary"), Mapping) else {},
        )
