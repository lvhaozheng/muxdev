"""Memory contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryRef:
    id: str
    layer: str = "project"
    scope_id: str | None = None
    claim: str | None = None
    promotion_state: str | None = None
