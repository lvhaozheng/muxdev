# Open-source architecture research and adoption record

This report records patterns adopted after reviewing primary project documentation. muxdev reimplements only the minimum semantics required by its four fixed workflows; none of the referenced workflow frameworks became a runtime dependency.

| Source | Observed mature pattern | muxdev adoption | Deliberately not adopted | Code proof |
|---|---|---|---|---|
| [OpenHands typed events](https://docs.openhands.dev/sdk/arch/events) | Immutable typed events form an append-only history; origin and presentation role are distinct. | Typed EvidenceRecord events, immutable event payloads, sequence and SHA-256 previous-hash chain. | Conversation memory, condensation, observer framework, and LLM message conversion. | `models/evidence.py`, `storage/control.py` |
| [Codex app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md) | Submission returns identity/state, progress is emitted as events, and approvals are explicit server requests with authoritative completion events. | Runs, events, interactions, and responses are separate resources; CLI/HTTP/MCP share the same TaskService lifecycle. | A general bidirectional conversation protocol, thread/turn hierarchy, transport variants, and experimental APIs. | `application/task_service.py`, `api/web.py`, `api/mcp.py` |
| [DBOS workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial) | Durable execution resumes after the last completed step, with persisted workflow state and timeouts. | Jobs and stage checkpoints are persisted; resume skips completed stages and refuses unsafe opaque replay. | Decorator-based workflow programming, queues, durable sleep, debouncing, and a DBOS dependency. | `runtime/engine.py`, `storage/control.py` |
| [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | A stable execution ID plus durable checkpoint supports pause, external response, and resume; resuming may rerun node code. | Stable run/interaction IDs, persisted pending approval, explicit response, and resume. The unsafe-replay rule comes directly from the documented node-restart hazard. | Dynamic graph construction, arbitrary conditional nodes, checkpointer plugins, and LangGraph itself. | `runtime/engine.py`, `storage/control.py` |
| [SWE-agent architecture](https://swe-agent.com/0.7/background/architecture/) | The Agent and execution environment are distinct; actions are executed through one environment boundary. | One ProviderAdapter method and one isolated worktree boundary; workspace facts are read by Runtime rather than accepted from the model. | A long-lived shell protocol, Agent-specific command language, and container lifecycle management. | `providers/contracts.py`, `runtime/worktree.py`, `runtime/workspace.py` |
| [Aider repository map](https://aider.chat/docs/repomap.html) | Concise paths and symbol signatures give a model global code context within a token budget. | On-demand deterministic AST symbol map ranked by task tokens, capped by file and character budgets. | Vector search, embedding index, long-term Memory, or persistent RAG. | `services/repo_map.py` |
| [in-toto Attestation Framework](https://github.com/in-toto/attestation/blob/main/spec/README.md) | Predicate contains metadata, Statement binds it to Subjects, and Envelope authenticates/serializes it. | Evidence report remains the business fact; optional DSSE contains a Statement that references the report digest. | Bundles, a new attestation runtime, and claims of in-toto policy conformance. | `services/dsse.py` |
| [SLSA provenance](https://slsa.dev/spec/v1.2/provenance) | Provenance is verifiable information about where, when, and how artifacts were produced. | Producer/verifier terminology and digest-bound subject identity. | SLSA level or compliance claims; muxdev is not a build-platform certification system. | `models/evidence.py`, `services/evidence_verify.py` |

## Why a minimal state machine is the rational choice

The product has four known workflows and two bounded conditions (`profile.strict` and at most two repair rounds). DBOS and LangGraph validate the checkpoint/interrupt semantics, but their generalized programming models would add more states, dependency surface, migration work, and operational failure modes than muxdev needs. The compact implementation can state and test its invariants directly:

1. Every externally visible state transition is persisted before the next side effect.
2. Completed stages are not replayed.
3. An interrupted opaque Provider stage is blocked unless Runtime can safely reconcile it.
4. Human response is a separate append-only fact, not mutation of model output.
5. Gate evaluation is reproducible from frozen Policy plus typed records.

## Interview conclusion

The engineering depth is in choosing and proving invariants, not importing frameworks. Each borrowed pattern maps to one product risk—lost state, forged evidence, replayed side effects, context bloat, or unverifiable claims—and every non-adopted feature has an explicit scope reason.
