"""The one execution port implemented by every Provider."""

from __future__ import annotations

from typing import Protocol

from ..domain import StageExecutionInput, StageExecutionResult


class ProviderAdapter(Protocol):
    def execute(self, input: StageExecutionInput) -> StageExecutionResult:
        """Execute one stage and return runtime-observable output."""
        ...
