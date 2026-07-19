"""Read-only export of muxdev's native workflow DAG."""

from __future__ import annotations

from typing import Any

from ..workflows import execution_batches, load_workflow, ordered_stage_ids


def workflow_graph(name_or_path: str) -> dict[str, Any]:
    """Return a provider-neutral graph without selecting another runtime."""
    workflow = load_workflow(name_or_path)
    return {
        "name": workflow.name,
        "runtime": "native",
        "nodes": [
            {
                "id": stage.id,
                "role": stage.role,
                "type": stage.type,
                "deps": list(stage.deps),
                "when": stage.when,
                "loop_review_stage": stage.loop_review_stage,
                "loop_restart_stage": stage.loop_restart_stage,
                "loop_reset_stages": list(stage.loop_reset_stages),
                "max_loops": stage.max_loops,
            }
            for stage in workflow.stages
        ],
        "edges": [
            {"source": dependency, "target": stage.id}
            for stage in workflow.stages
            for dependency in stage.deps
        ]
        + [
            {
                "source": stage.id,
                "target": stage.loop_restart_stage,
                "kind": "conditional_loop",
                "condition": stage.when,
                "max_loops": stage.max_loops,
            }
            for stage in workflow.stages
            if stage.loop_restart_stage
        ],
        "ordered_stage_ids": ordered_stage_ids(workflow),
        "execution_batches": execution_batches(workflow),
    }


__all__ = ["workflow_graph"]
