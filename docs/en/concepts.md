# Core Concepts and Quick Reference

[中文](../cn/concepts.md)

<!-- section:quick-reference -->
## Quick Reference

| Concept | One-sentence meaning | Primary code entry |
|---|---|---|
| Task / Run / Stage | User intent, one execution instance, and one DAG unit | `domain/run.py`, `workflows/`, `runtime/supervisor.py` |
| Workflow DAG | A dependency graph that decides what may run next | `workflows/`, `services/orchestration.py` |
| Provider / Adapter / Harness | External agent, translation boundary, and lifecycle contract | `providers/adapters.py`, `providers/harness.py` |
| Routing / Certification | Safe Provider eligibility and evidence-backed capability identity | `services/routing.py`, `providers/certification.py` |
| Worktree / Parallel merge | Isolated changes converted into verified patches | `runtime/parallel_merge.py`, `runtime/worktree.py` |
| Daemon / Lease / Fencing | Durable workers with expiring ownership and stale-writer rejection | `daemon/queue.py`, `storage/execution_queue.py` |
| Blackboard / Event / SQLite | Durable source of task state plus replayable transitions | `storage/blackboard.py`, `domain/state_events.py` |
| Approval / Provider Action | muxdev policy choice versus external CLI interaction | `application/lifecycle.py`, `domain/provider_actions.py` |
| Context / RAG / Memory | Bounded current input, retrieved source, and governed knowledge | `context/assembler.py`, `services/rag.py`, `storage/memory.py` |
| Evidence / Ledger / Attestation | Observations, tamper-evident history, and signed delivery statement | `services/evidence.py`, `storage/ledger.py`, `services/attestation.py` |
| Validation gates | Structured checks that fail closed | `runtime/result_validation.py`, `services/delivery_gate.py` |
| Snapshot / Recovery / Rollback | Restore execution safely without erasing history | `runtime/recovery.py`, `daemon/tasks.py` |

<!-- section:task-run-stage -->
## Task, Run, and Stage

**Definition.** A Task is the user-visible request. A Run is one durable attempt to execute it. A Stage is the smallest scheduled unit in that Run, such as plan, implement, test, or review.

**Analogy.** A Task is a course assignment, a Run is one submitted attempt, and Stages are research, drafting, checking, and submission.

**Why it exists.** Separating these levels lets one Task survive retries and lets each Stage carry its own role, Provider, policy, output contract, evidence, and status.

**Execution chain.** Entry surface creates `RunSpec` -> queue creates an execution -> scheduler selects a runnable Stage -> adapter returns `StageExecutionResult` -> lifecycle event updates the Run projection.

**Failure example.** A test Stage can fail while implementation output still exists. The Run becomes blocked or repairable; the Task is not lost.

**Code entry.** `src/muxdev/domain/run.py`, `src/muxdev/workflows/`, `src/muxdev/runtime/supervisor.py`.

**Common misconception.** “Task” and “Run” are not synonyms. Continuing or replaying normally creates another execution against the same durable Run identity and history.

<!-- section:workflow-dag -->
## Workflow DAG

**Definition.** A Directed Acyclic Graph describes Stages as nodes and prerequisites as directed edges. muxdev also stores bounded conditional-loop metadata, but the scheduler remains native.

**Analogy.** A university prerequisite chart allows Advanced Databases only after Databases; independent electives can run in parallel.

**Why it exists.** A DAG makes ordering inspectable, enables safe parallel batches, and avoids burying control flow in prompts.

**Execution chain.** YAML is validated -> dependencies are topologically ordered -> conditions and completed projections filter runnable nodes -> sequential or parallel scheduler executes them -> bounded loop metadata can reopen a review/fix segment.

**Failure example.** Cycles, missing dependencies, or an unbounded repair loop are rejected or blocked instead of guessed.

**Code entry.** `src/muxdev/workflows/`, `src/muxdev/config/defaults/workflows.yaml`, `src/muxdev/services/orchestration.py`.

**Common misconception.** A workflow is not a Provider plugin. It is provider-neutral orchestration data; the same DAG can use Mock, Codex, Qwen, or Replay.

<!-- section:provider-adapter-harness -->
## Provider, Adapter, and Harness

**Definition.** A Provider is the external coding agent. An Adapter translates muxdev’s typed contract to that Provider. The Harness is the wider lifecycle: probe, certify, execute, stream events, cancel, and resume.

**Analogy.** A wall socket supplies electricity, a travel adapter converts the plug, and an electrical safety standard defines voltage and grounding expectations.

**Why it exists.** Provider CLIs differ in flags, JSONL, prompt transport, sessions, approvals, and errors. One typed boundary prevents those differences leaking into the scheduler.

**Execution chain.** Runtime builds `StageExecutionInput` -> `execute(input)` starts a managed attempt -> parser normalizes provider-specific events -> adapter returns `StageExecutionResult` -> runtime validates and persists it.

**Failure example.** A CLI exits zero but returns prose instead of `TestResult`; the adapter succeeded as a process, while the validation gate correctly fails the Stage.

**Code entry.** `src/muxdev/domain/stage.py`, `src/muxdev/runtime/stage_executor.py`, `src/muxdev/providers/adapters.py`, `src/muxdev/providers/harness.py`, `src/muxdev/providers/event_parsers.py`.

**Common misconception.** Provider detection is not proof of safe capability. A help flag can be advertised while certification remains missing or stale.

<!-- section:routing-certification -->
## Routing and Certification

**Definition.** Routing chooses an eligible Provider for a role. Certification binds observed capabilities to a Provider executable fingerprint, adapter version, and evidence.

**Analogy.** A hospital does not assign surgery to whoever is nearby; it first checks specialty, license, availability, and conflict-of-interest rules.

**Why it exists.** Cost or historical score must never override hard requirements such as read-only review, sandboxing, production mode, or an allowlist.

**Execution chain.** Extract bounded task features -> filter by policy and certified capabilities -> apply conservative quality lower bound -> record immutable decision -> optionally assign a distinct reviewer -> re-check before execution.

**Failure example.** Updating a CLI changes its fingerprint. Its previous certification becomes stale, so a high-risk task pauses before the Provider starts.

**Code entry.** `src/muxdev/services/routing.py`, `src/muxdev/services/routing_review.py`, `src/muxdev/providers/certification.py`.

**Common misconception.** Auto routing is not an unrestricted model leaderboard; it is a policy-constrained decision using limited local evidence.

<!-- section:worktree-parallel-merge -->
## Worktree and Parallel Merge

**Definition.** A worktree is a task-scoped checkout. Parallel writers receive further isolated worker workspaces whose changes become content-addressed patches.

**Analogy.** Two students edit separate copies of a lab report, then submit labeled change sheets to a coordinator instead of typing into the same document.

**Why it exists.** Shared mutable directories create races, invisible overwrites, and non-reproducible diffs.

**Execution chain.** Prepare baseline -> hash workspace -> clone worker directories -> execute stages -> capture touched files and binary patch -> verify base hash and overlap -> apply in deterministic order -> semantic review.

**Failure example.** Two Stages edit `auth.py`; overlap detection blocks the merge before either patch silently wins. Conflict markers also block delivery.

**Code entry.** `src/muxdev/runtime/worktree.py`, `src/muxdev/runtime/parallel_merge.py`, `src/muxdev/services/semantic_merge.py`.

**Common misconception.** Parallelism is not simply a thread pool. Isolation and deterministic reconciliation are the essential parts.

<!-- section:daemon-lease-fencing -->
## Daemon, Lease, and Fencing

**Definition.** The daemon owns durable jobs. A lease grants temporary execution ownership; a fencing number lets storage reject writes from an older owner.

**Analogy.** A library study room reservation expires, and each new reservation gets a higher ticket number. Someone holding yesterday’s ticket cannot lock the room today.

**Why it exists.** Processes crash, machines restart, cancellation races happen, and two workers can briefly believe they own the same job.

**Execution chain.** Enqueue transaction -> worker atomically claims job -> heartbeat extends lease -> execution guard checks cancellation/fence -> completion commits only with current fence -> expired work is retried or reconciled according to risk.

**Failure example.** Worker A freezes; Worker B reclaims the job. When A wakes, its stale fence is rejected, preventing a second completion.

**Code entry.** `src/muxdev/daemon/queue.py`, `src/muxdev/storage/executions.py`, `src/muxdev/domain/execution.py`.

**Common misconception.** A mutex is not enough. A mutex disappears with a process; durable leases and fencing survive process boundaries.

<!-- section:blackboard-event-sqlite -->
## Blackboard, Event, and SQLite

**Definition.** Blackboard is the local persistence façade. Core lifecycle changes are events; query-friendly tables are projections in SQLite.

**Analogy.** A bank keeps an immutable transaction journal and also maintains a current balance. The balance is convenient, but the journal explains how it arose.

**Why it exists.** Events support replay and audit; projections make task status fast. A transaction commits both together or neither.

**Execution chain.** Validate transition -> append event with idempotency key and sequence -> update projection in the same transaction -> run post-commit notifications -> read through repositories/read models.

**Failure example.** A projection update throws; the event rolls back too, so history never claims a state change that readers cannot see.

**Code entry.** `src/muxdev/storage/blackboard.py`, `src/muxdev/storage/schema.py`, `src/muxdev/storage/repositories/`, `src/muxdev/domain/state_events.py`, `src/muxdev/storage/read_models/`.

**Common misconception.** SQLite is not “just a cache.” It is the durable local control-plane database; deleting it destroys recovery context.

<!-- section:approval-provider-action -->
## Approval and Provider Action

**Definition.** An Approval asks a human to accept a muxdev policy subject. A Provider Action reports that an external CLI needs interaction or remediation.

**Analogy.** Building management approves access to a secure room; once inside, a machine may separately ask an operator to replace paper. These are different authorities.

**Why it exists.** Treating every prompt as approval enables confused-deputy bugs and makes subject drift invisible.

**Execution chain.** Policy computes subject hash -> pending Approval pauses Run -> decision is recorded -> runtime revalidates subject. Separately, stream parser emits `DetectedProviderAction` -> runtime binds run/stage/provider into `ProviderActionRequest` -> response is recorded -> user continues.

**Failure example.** The plan changes after approval. The old subject hash no longer matches, so muxdev requests new approval rather than reusing stale consent.

**Code entry.** `src/muxdev/application/lifecycle.py`, `src/muxdev/clients/stream.py`, `src/muxdev/domain/provider_actions.py`.

**Common misconception.** muxdev does not automatically type “yes” into Provider CLIs. Provider interaction remains explicit.

<!-- section:context-rag-memory -->
## Context, RAG, and Memory

**Definition.** Context is the bounded packet for the current Stage. RAG retrieves relevant workspace excerpts. Memory stores governed knowledge that may outlive a Run.

**Analogy.** Context is the material on your desk, RAG is a library search, and Memory is a curated notebook. Search results do not become notebook facts automatically.

**Why it exists.** Unlimited prompts exceed token budgets, stale facts mislead agents, and untrusted notes can override current blockers.

**Execution chain.** Gather task and P0 blockers -> score sources -> retrieve citations when useful -> add active evidence-grounded Memory -> enforce deterministic budget -> write context packet/hash -> bind packet to Stage input.

**Failure example.** Two Memory items contradict each other; the lower-trust item is quarantined instead of silently injected.

**Code entry.** `src/muxdev/context/assembler.py`, `src/muxdev/context/budget.py`, `src/muxdev/services/rag.py`, `src/muxdev/storage/memory.py`.

**Common misconception.** RAG and Memory are not the same. RAG is retrieval for now; Memory requires lifecycle and trust governance.

<!-- section:evidence-ledger-attestation -->
## Evidence, Ledger, and Attestation

**Definition.** Evidence records observations supporting claims. The ledger hash-chains important records. An attestation signs a bounded statement about a completed delivery.

**Analogy.** Evidence is lab measurements, the ledger is the numbered lab notebook, and attestation is the signed conclusion referencing those measurements.

**Why it exists.** Natural-language confidence is not proof. Consumers need to know which tests ran, which artifacts were hashed, and whether identity or history is incomplete.

**Execution chain.** Stage emits artifacts/events -> Evidence manifest evaluates coverage -> ledger appends canonical hashes -> completion collects allowlisted facts -> project key signs payload -> optional `.muxattest` bundle verifies offline.

**Failure example.** An artifact changes after signing. Its hash differs and bundle verification fails. A missing key yields explicitly unsigned output rather than a forged identity.

**Code entry.** `src/muxdev/services/evidence.py`, `src/muxdev/storage/ledger.py`, `src/muxdev/services/attestation.py`, `src/muxdev/services/attestation_bundle.py`.

**Common misconception.** A valid signature proves payload integrity and key possession, not that every claim inside is objectively true or organization-endorsed.

<!-- section:validation-gates -->
## Validation Gates

**Definition.** Gates are deterministic checks that must pass before a Stage or delivery can advance: schema, test, review, policy, budget, isolation, evidence, and artifact checks.

**Analogy.** A compiler accepting syntax does not mean the program is correct; type checks, tests, review, and release checks answer different questions.

**Why it exists.** Provider prose like “all tests passed” is ambiguous and easy to hallucinate.

**Execution chain.** Parse JSON object -> validate `TestResult` or `ReviewResult` -> compare boolean fields with exit codes/blockers -> record contract evidence -> evaluate delivery standard -> block, repair, or finalize.

**Failure example.** `passed: true` with `exit_code: 1` is internally inconsistent and becomes a failed result. Missing Review JSON creates a high-severity blocker.

**Code entry.** `src/muxdev/runtime/result_validation.py`, `src/muxdev/services/delivery_gate.py`, `src/muxdev/storage/contracts.py`.

**Common misconception.** Exit code zero only means the Provider process ended normally; it does not clear the delivery gate.

<!-- section:recovery-rollback -->
## Snapshot, Recovery, and Rollback

**Definition.** A snapshot captures a known execution boundary. Recovery reconciles durable ownership and incomplete state. Rollback restores a selected filesystem snapshot while preserving audit history.

**Analogy.** Versioned save points let you reopen a game after a crash; the activity log still records that the crash and restore occurred.

**Why it exists.** Blind retries can duplicate side effects, and destructive resets erase the evidence needed to decide what is safe.

**Execution chain.** Detect blocked/expired execution -> classify attempt safety -> require reconciliation for opaque side effects -> reset eligible Stage projection with reason -> restore snapshot or resume -> acquire a new fenced lease -> continue.

**Failure example.** A lease is lost after an opaque Provider may have written externally. muxdev refuses automatic retry until a human reconciles the side effect.

**Code entry.** `src/muxdev/runtime/recovery.py`, `src/muxdev/daemon/tasks.py`, `src/muxdev/storage/executions.py`.

**Common misconception.** Rollback is not `git reset --hard` and does not rewrite the database. It is a controlled recovery event with retained history.
