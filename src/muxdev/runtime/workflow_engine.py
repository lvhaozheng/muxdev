"""Workflow compilation helpers for the runtime kernel."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import WorkflowDefinition
from ..workflows import execution_batches, ordered_stage_ids, should_run_when


@dataclass(frozen=True)
class WorkflowPlan:
    workflow: WorkflowDefinition
    ordered_stage_ids: tuple[str, ...]
    execution_batches: tuple[tuple[str, ...], ...]


class WorkflowEngine:
    def compile(self, workflow: WorkflowDefinition) -> WorkflowPlan:
        return WorkflowPlan(
            workflow=workflow,
            ordered_stage_ids=tuple(ordered_stage_ids(workflow)),
            execution_batches=tuple(tuple(batch) for batch in execution_batches(workflow)),
        )

    def should_run(self, expression: str | None, context: dict[str, object]) -> bool:
        return should_run_when(expression, context)
