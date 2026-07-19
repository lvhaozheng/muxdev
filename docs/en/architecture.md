# Architecture

[中文](../cn/architecture.md)

<!-- section:boundaries -->
## Layer Boundaries

The dependency direction is inward: `cli`/`api`/presentation are entry surfaces; application services expose use cases; daemon coordination owns queues; runtime executes stages; domain contains stable contracts; storage and Provider adapters implement ports. Domain code must not import runtime, storage, daemon, API, CLI, or UI. API handlers call task command/query services and never instantiate `SupervisorRuntime` or open SQLite.

<!-- section:request-flow -->
## Request Flow

```text
CLI / HTTP / TUI
  -> TaskCommandService
  -> durable RunSpec + execution job
  -> bounded worker pool and fenced lease
  -> SupervisorRuntime facade
  -> native Workflow DAG scheduler
  -> StageExecutionInput -> Provider adapter -> StageExecutionResult
  -> validation, Evidence, projections, report, attestation
```

Queries follow a separate path through `TaskQueryService` and read models. This keeps status rendering from accidentally starting work or changing state.

<!-- section:runtime -->
## Runtime and Stage Execution

`SupervisorRuntime` is the external façade. The scheduler calculates runnable stages from dependencies and conditions. Every Provider implements one typed method: `execute(StageExecutionInput) -> StageExecutionResult`. The input binds run, stage, role, Provider, worktree, policy, context, Skills, session directory, and attempt. The result carries content, artifact identity, usage, events, exit status, and detected actions. Test/Review schemas are validated fail-closed; process exit code zero alone never proves success.

<!-- section:parallel -->
## Parallel Isolation and Merge

Independent writable stages receive separate worker workspaces. Each workspace is hashed, its binary-capable patch is captured, and the patch is bound to the baseline hash. The deterministic merge rejects base drift, overlapping writes, conflict markers, and semantic-review blockers. Read-only stages are checked for writes. A parallel stage never shares a mutable checkout with another writer.

<!-- section:storage -->
## Durable State

Blackboard uses SQLite schema v7 with WAL/FULL durability, checksummed migrations, transactions, event-first lifecycle changes, and read projections. Runs keep task context, workflow snapshots, provider transcripts, artifacts, diffs, reports, Evidence, ledger entries, and attestations under controlled directories. Durable jobs use leases, heartbeats, cancellation tokens, fencing numbers, and reconciliation so a stale worker cannot commit after ownership is lost. Archived `langgraph` metadata maps to the native scheduler during restore; the database layout is not reset.

<!-- section:extension -->
## Extension Points

- Provider adapters: typed execution plus optional probe, certification, events, cancel, and resume.
- Workflows: YAML DAG stages, dependencies, conditions, gates, schemas, and loop metadata.
- Skills: governed prompt/context packages selected explicitly or by trusted bindings.
- Context: bounded task, RAG, Memory, blocker, and Provider-response sources.
- Validation: structured output contracts, delivery gates, benchmark replay, and independent review.

See [Development](development.md) for rules and [Concepts](concepts.md) for deeper explanations.
