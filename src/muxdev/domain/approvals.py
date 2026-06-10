"""Approval contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ApprovalRequest:
    run_id: str
    approval_type: str
    reason: str
    stage_id: str | None = None
    subject: Mapping[str, object] = field(default_factory=dict)
