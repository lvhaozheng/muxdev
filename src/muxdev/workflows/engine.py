"""Validation and deterministic ordering for four fixed workflows."""

from __future__ import annotations

from collections import defaultdict, deque

from ..config.loader import load_config
from ..models import WorkflowDefinition


def load_workflow(name: str) -> WorkflowDefinition:
    """Load one of change, design, review, or test."""
    workflows = load_config().get("workflows", {})
    if name not in {"change", "design", "review", "test"} or name not in workflows:
        raise ValueError(f"unknown workflow: {name}")
    data = workflows[name]
    workflow = WorkflowDefinition.model_validate(data)
    validate_dag(workflow)
    return workflow


def validate_dag(workflow: WorkflowDefinition) -> None:
    """Reject missing dependencies and cycles before execution begins."""
    stage_ids = {stage.id for stage in workflow.stages}
    for stage in workflow.stages:
        missing = [dep for dep in stage.deps if dep not in stage_ids]
        if missing:
            raise ValueError(f"stage {stage.id} has unknown deps: {', '.join(missing)}")
    ordered_stage_ids(workflow)


def ordered_stage_ids(workflow: WorkflowDefinition) -> list[str]:
    """Return a deterministic topological order for serial execution."""
    indegree = {stage.id: 0 for stage in workflow.stages}
    graph: dict[str, list[str]] = defaultdict(list)
    for stage in workflow.stages:
        for dep in stage.deps:
            graph[dep].append(stage.id)
            indegree[stage.id] += 1
    queue = deque([stage.id for stage in workflow.stages if indegree[stage.id] == 0])
    ordered: list[str] = []
    while queue:
        stage_id = queue.popleft()
        ordered.append(stage_id)
        for child in graph[stage_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(ordered) != len(workflow.stages):
        raise ValueError(f"workflow {workflow.name} contains a cycle")
    return ordered


def should_run_when(expression: str | None, context: dict[str, object]) -> bool:
    """Evaluate the two conditions used by the fixed workflow definitions."""
    if not expression:
        return True
    if expression == "profile.strict":
        profile = context.get("profile", {})
        return bool(profile.get("strict")) if isinstance(profile, dict) else False
    if expression == "review.has_blockers and loop < 2":
        review = context.get("review", {})
        has_blockers = bool(review.get("has_blockers")) if isinstance(review, dict) else False
        return has_blockers and int(context.get("loop", 0)) < 2
    raise ValueError(f"unsupported workflow condition: {expression}")
