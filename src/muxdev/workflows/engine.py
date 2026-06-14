"""Workflow loading and DAG utilities.

Workflows can come from a YAML file path or from the merged dynamic config. The
engine validates dependencies, returns deterministic topological order, and
groups independent stages into batches for the supervisor's safe parallel path.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from pathlib import Path

import yaml

from ..config.loader import load_config
from ..models import WorkflowDefinition


SOFTWARE_DEV_WORKFLOW = yaml.safe_dump(load_config().get("workflows", {}).get("software-dev", {}), sort_keys=False)


def load_workflow(name_or_path: str) -> WorkflowDefinition:
    """Load a workflow by filesystem path or configured workflow name."""
    path = Path(name_or_path)
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        workflows = load_config().get("workflows", {})
        if name_or_path not in workflows:
            raise ValueError(f"unknown workflow: {name_or_path}")
        data = workflows[name_or_path]
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


def execution_batches(workflow: WorkflowDefinition) -> list[list[str]]:
    """Return dependency-safe batches for parallel stage execution."""
    indegree = {stage.id: 0 for stage in workflow.stages}
    graph: dict[str, list[str]] = defaultdict(list)
    order = {stage.id: index for index, stage in enumerate(workflow.stages)}
    for stage in workflow.stages:
        for dep in stage.deps:
            graph[dep].append(stage.id)
            indegree[stage.id] += 1

    ready = [stage.id for stage in workflow.stages if indegree[stage.id] == 0]
    batches: list[list[str]] = []
    seen = 0
    while ready:
        batch = sorted(ready, key=order.__getitem__)
        batches.append(batch)
        seen += len(batch)
        next_ready: list[str] = []
        for stage_id in batch:
            for child in graph[stage_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    next_ready.append(child)
        ready = next_ready
    if seen != len(workflow.stages):
        raise ValueError(f"workflow {workflow.name} contains a cycle")
    return batches


def should_run_when(expression: str | None, context: dict[str, object]) -> bool:
    """Evaluate the intentionally tiny workflow condition language."""
    if not expression:
        return True
    normalized = expression.replace("&&", "and")
    if normalized == "review.has_blockers and loop < 2":
        review = context.get("review", {})
        has_blockers = bool(review.get("has_blockers")) if isinstance(review, dict) else False
        loop = int(context.get("loop", 0))
        return has_blockers and loop < 2
    match = re.fullmatch(r"([A-Za-z_][\w]*)\.has_blockers\s+and\s+loop\s+<\s+([A-Za-z_][\w]*|\d+)", normalized.strip())
    if match:
        review = context.get(match.group(1), {})
        has_blockers = bool(review.get("has_blockers")) if isinstance(review, dict) else False
        loop = int(context.get("loop", 0))
        limit_token = match.group(2)
        limit = int(limit_token) if limit_token.isdigit() else int(context.get(limit_token, 0) or 0)
        return has_blockers and loop < limit
    return False
