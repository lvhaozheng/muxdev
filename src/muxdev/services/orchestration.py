"""Workflow export helpers for external orchestration systems."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..workflows import load_workflow


def workflow_to_langgraph(name_or_path: str) -> dict[str, Any]:
    from ..runtime.langgraph_engine import LangGraphWorkflowEngine

    workflow = load_workflow(name_or_path)
    return LangGraphWorkflowEngine(Path.cwd()).graph_spec(workflow)


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
