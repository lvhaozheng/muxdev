"""Workflow export helpers for external orchestration systems."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..workflows import load_workflow


def workflow_to_langgraph(name_or_path: str) -> dict[str, Any]:
    workflow = load_workflow(name_or_path)
    nodes = [
        {
            "id": stage.id,
            "role": stage.role,
            "type": stage.type,
            "metadata": {
                "read_only": stage.read_only,
                "allow_write": stage.allow_write,
                "allow_shell": stage.allow_shell,
                "checkpoint": stage.checkpoint,
                "when": stage.when,
            },
        }
        for stage in workflow.stages
    ]
    edges = [{"source": dep, "target": stage.id} for stage in workflow.stages for dep in stage.deps]
    return {"name": workflow.name, "max_parallel": workflow.max_parallel, "nodes": nodes, "edges": edges}


def deep_agent_task_pack(task: str, workflow_name: str, workspace: Path) -> dict[str, Any]:
    graph = workflow_to_langgraph(workflow_name)
    agents = sorted({node["role"] for node in graph["nodes"] if node["role"]})
    return {
        "task": task,
        "workspace": str(workspace),
        "graph": graph,
        "agents": [
            {
                "role": role,
                "instructions": f"Act as the muxdev {role}. Follow graph dependencies and write artifacts to the run blackboard.",
            }
            for role in agents
        ],
    }


__all__ = ["deep_agent_task_pack", "workflow_to_langgraph"]
