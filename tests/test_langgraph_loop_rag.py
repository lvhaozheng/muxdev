from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from muxdev.context import build_context_packet, task_with_context_packet
from muxdev.runtime import LangGraphWorkflowEngine, SupervisorRuntime
from muxdev.workflows import load_workflow


def test_langgraph_graph_spec_preserves_loop_metadata() -> None:
    workflow = load_workflow("design-v2")

    spec = LangGraphWorkflowEngine(Path.cwd()).graph_spec(workflow)

    assert spec["runtime"] == "langgraph"
    assert workflow.name == "design"
    assert "design_revise" in spec["ordered_stage_ids"]
    loop_edges = [edge for edge in spec["edges"] if edge.get("kind") == "conditional_loop"]
    assert loop_edges
    assert loop_edges[0]["condition"] == "design_verify.has_blockers && loop < max_loops || plan_feedback.has_feedback"


def test_context_packet_records_rag_decision_and_citations() -> None:
    workspace = _workspace_temp("rag-context")
    try:
        (workspace / "notes.md").write_text("muxdev retrieval target lives here\n", encoding="utf-8")

        packet = build_context_packet(
            run_id="run_rag",
            stage_id="design",
            role="architect",
            provider="mock",
            workflow="dev",
            task="基于现有实现查找 retrieval target",
            worktree=workspace,
            skills=[],
            automation={},
            provider_attempts=[],
            context_sources=["rag"],
            rag_query="retrieval target",
            loop_state={"iteration": 1},
        )

        assert packet["rag_decision"]["enabled"] is True
        assert packet["rag_context"]
        assert packet["rag_context"][0]["citation"].startswith("notes.md:")
        assert packet["loop_state"]["iteration"] == 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_task_with_context_packet_inlines_handled_provider_responses() -> None:
    workspace = _workspace_temp("provider-response-prompt")
    try:
        packet_path = workspace / "design_brief.json"
        packet_path.write_text(
            json.dumps(
                {
                    "task": {
                        "provider_action_responses": [
                            {
                                "stage_id": "design_brief",
                                "kind": "cli_confirmation",
                                "response": {"text": "platform=Web; style=cartoon; controls=keyboard arrows"},
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        prompt = task_with_context_packet("Design a snake game", packet_path, "sha256:test")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert "Previously Handled Provider Actions" in prompt
    assert "platform=Web; style=cartoon; controls=keyboard arrows" in prompt
    assert "do not ask the same confirmation again" in prompt


def test_supervisor_defaults_to_langgraph_wrapper_trace() -> None:
    workspace = _workspace_temp("langgraph-runtime")
    try:
        result = SupervisorRuntime(workspace).run("review this tiny task", provider="mock", workflow_name="review")
        trace = (result.run_dir / "trace.jsonl").read_text(encoding="utf-8")
        events = [json.loads(line) for line in trace.splitlines() if line.strip()]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert any(event["type"] == "langgraph_runtime_selected" for event in events)
    assert any(event["type"] == "langgraph_execution_completed" for event in events)


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
