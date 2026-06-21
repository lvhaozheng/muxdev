# LangGraph And Loop Engineering Upgrade

This document explains the current muxdev architecture upgrade for interview-oriented LLM application development.

## Positioning

muxdev is now framed as a local-first Agentic SDLC loop platform:

- LangGraph is the workflow control-plane target for DAGs, human gates, checkpoints, and loop semantics.
- The existing SupervisorRuntime remains the stable facade for CLI, daemon, dashboard, validation, and tests.
- Loop Engineering means plan, execute, validate, repair, and promote durable learning instead of making one-shot model calls.
- RAG is optional task context, not a default prompt injection strategy.

## Runtime Flow

Existing commands still call the same runtime path:

```text
CLI / API
  -> TaskRuntimeService
  -> SupervisorRuntime.run / resume
  -> LangGraphWorkflowEngine
  -> muxdev native audited stage execution
  -> blackboard / trace / context packet / evidence / report
```

`LangGraphWorkflowEngine` compiles `WorkflowDefinition` into graph metadata and builds a LangGraph `StateGraph` when the package is available. During the migration window, stage execution delegates to muxdev's audited native stage path so approvals, provider actions, evidence, and recovery remain compatible.

## Loop Engineering

Workflow stages can now declare `loop_policy`, `context_sources`, and `rag_query`.

The runtime records loop events:

- `loop_started`
- `loop_iteration_completed`
- `loop_stopped`
- `loop_blocked`

Evidence v2 converts these trace events into runtime evidence tagged with `loop_engineering`, and validation reports summarize loop iterations and blocked loops.

## RAG Policy

RAG is enabled only when one of these is true:

- A stage explicitly includes `rag` in `context_sources`.
- The task asks for codebase, architecture, module, location, or existing-implementation context.
- Memory is insufficient and source citations are useful.

RAG is skipped for simple tests, approval handling, provider-action handling, and low-risk tasks without retrieval intent. Each context packet records `rag_decision` and, when enabled, `rag_context` with citations.

## Validation Signals

Validation metrics now include:

- `workflow_engine`
- `loop_iterations`
- `loop_blocked`
- `retrieval_used`
- `citation_coverage`
- `retrieval_hit_rate`
- `checkpoint_recovery`

These fields make it possible to compare native compatibility, LangGraph orchestration, optional RAG, and loop repair behavior without adding new commands.

