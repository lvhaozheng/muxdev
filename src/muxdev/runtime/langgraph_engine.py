"""LangGraph-compatible workflow compilation and runtime adapter.

The current SupervisorRuntime remains the public facade. This module gives the
runtime a LangGraph-first control plane without changing CLI/API entrypoints:
workflow definitions are compiled into graph metadata and, when langgraph is
available, a real StateGraph object is built for validation and introspection.
Stage execution still delegates to muxdev's audited supervisor path during the
migration so blackboard, approvals, evidence, and provider behavior stay stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..models import WorkflowDefinition, WorkflowStage
from ..storage import TraceWriter
from ..workflows import ordered_stage_ids


NativeExecutor = Callable[[], Any]


@dataclass
class WorkflowGraphState:
    run_id: str
    task: str
    workflow: str
    current_stage: str | None = None
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    loop: int = 0
    stage_results: dict[str, object] = field(default_factory=dict)
    approval_waiting: bool = False
    provider_action_waiting: bool = False
    status: str = "running"

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "workflow": self.workflow,
            "current_stage": self.current_stage,
            "completed": list(self.completed),
            "skipped": list(self.skipped),
            "loop": self.loop,
            "stage_results": dict(self.stage_results),
            "approval_waiting": self.approval_waiting,
            "provider_action_waiting": self.provider_action_waiting,
            "status": self.status,
        }


class LangGraphWorkflowEngine:
    """Compile muxdev workflows to LangGraph semantics and execute via muxdev."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def graph_spec(self, workflow: WorkflowDefinition) -> dict[str, object]:
        nodes = [_node_spec(stage) for stage in workflow.stages]
        edges = [{"source": dep, "target": stage.id, "kind": "dependency"} for stage in workflow.stages for dep in stage.deps]
        for stage in workflow.stages:
            if stage.when:
                edges.append(
                    {
                        "source": stage.id,
                        "target": stage.loop_restart_stage or _default_loop_target(stage),
                        "kind": "conditional_loop",
                        "condition": stage.when,
                        "max_iterations": _stage_max_iterations(stage),
                    }
                )
        return {
            "name": workflow.name,
            "runtime": "langgraph",
            "backend": "langgraph" if _langgraph_available() else "muxdev-compat",
            "max_parallel": workflow.max_parallel,
            "ordered_stage_ids": ordered_stage_ids(workflow),
            "nodes": nodes,
            "edges": edges,
        }

    def build_state_graph(self, workflow: WorkflowDefinition) -> Any | None:
        """Build a real LangGraph StateGraph when the package is installed."""
        try:
            from langgraph.graph import END, StateGraph
            from typing_extensions import TypedDict
        except Exception:
            return None

        class State(TypedDict, total=False):
            run_id: str
            task: str
            workflow: str
            current_stage: str
            completed: list[str]
            skipped: list[str]
            loop: int
            stage_results: dict[str, object]
            approval_waiting: bool
            provider_action_waiting: bool
            status: str

        graph = StateGraph(State)
        for stage in workflow.stages:
            graph.add_node(stage.id, _state_node(stage))
        ordered = ordered_stage_ids(workflow)
        if ordered:
            graph.set_entry_point(ordered[0])
        for source, target in zip(ordered, ordered[1:]):
            graph.add_edge(source, target)
        if ordered:
            graph.add_edge(ordered[-1], END)
        return graph

    def execute(self, *, workflow: WorkflowDefinition, run_id: str, task: str, trace: TraceWriter, native_executor: NativeExecutor) -> Any:
        spec = self.graph_spec(workflow)
        built = self.build_state_graph(workflow)
        trace.write(
            "langgraph_runtime_selected",
            backend=spec["backend"],
            workflow=workflow.name,
            nodes=[node["id"] for node in spec["nodes"] if isinstance(node, dict)],
            compiled=bool(built),
        )
        state = WorkflowGraphState(run_id=run_id, task=task, workflow=workflow.name)
        trace.write("langgraph_state_initialized", state=state.to_dict())
        result = native_executor()
        trace.write("langgraph_execution_completed", backend=spec["backend"], status=str(getattr(result, "status", "")))
        return result


def _node_spec(stage: WorkflowStage) -> dict[str, object]:
    return {
        "id": stage.id,
        "role": stage.role,
        "type": stage.type,
        "metadata": {
            "read_only": stage.read_only,
            "allow_write": stage.allow_write,
            "allow_shell": stage.allow_shell,
            "checkpoint": stage.checkpoint,
            "when": stage.when,
            "loop_policy": stage.loop_policy.model_dump() if stage.loop_policy else None,
            "context_sources": list(stage.context_sources),
            "rag_query": stage.rag_query,
            "delivery_targets": list(stage.delivery_targets),
            "delivery_skill_sources": list(stage.delivery_skill_sources),
            "max_loops": stage.max_loops,
            "loop_review_stage": stage.loop_review_stage,
            "loop_restart_stage": stage.loop_restart_stage,
        },
    }


def _state_node(stage: WorkflowStage):
    def node(state: dict[str, object]) -> dict[str, object]:
        completed = list(state.get("completed") or [])
        if stage.id not in completed:
            completed.append(stage.id)
        return {**state, "current_stage": stage.id, "completed": completed}

    return node


def _default_loop_target(stage: WorkflowStage) -> str:
    if stage.id == "fix":
        return "test"
    if stage.loop_review_stage:
        return stage.loop_review_stage
    if stage.id.endswith("_revise"):
        return stage.id[: -len("_revise")] + "_review"
    return stage.id


def _stage_max_iterations(stage: WorkflowStage) -> int | None:
    if stage.loop_policy and stage.loop_policy.max_iterations is not None:
        return stage.loop_policy.max_iterations
    return stage.max_loops


def _langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401
    except Exception:
        return False
    return True
