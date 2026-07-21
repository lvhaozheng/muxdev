# muxdev English reference

muxdev is a local-first trusted-delivery control plane for AI agents, not a general multi-agent platform.

It exposes four workflows (`change`, `design`, `review`, `test`), three Profiles (`lite`, `standard`, `strict`), five built-in Skills, one Provider execution method, and one deterministic EvidencePolicy/Gate Engine. TaskService owns lifecycle use cases; RunEngine owns durable stage execution and recovery. Strict changes fan ordinary and security review out over one frozen read-only subject and commit their results in deterministic order.

Provider output is a claim. Provider-suggested commands are never executed: only frozen Workflow/project `VerificationCommand` argv arrays can produce an `ExecutedCheck` and trusted exit code. The runtime also derives facts from conflict-safe ChangeSets, subject-bound reviews, runtime-confirmed reviewer identity, human interactions, and a hash-chained event log. Hard gates produce `PASS`, `BLOCKED`, or `WAITING_HUMAN`.

```powershell
python -m pip install -e ".[test]"
muxdev init
muxdev run "add a deterministic marker" --workflow change --profile lite --provider mock
muxdev evidence verify <run-id>
```

Each run produces one `evidence-report.json`. Explicit export can add an `attestation.dsse.json` that binds the report digest without duplicating its facts. Project Skills remain prompt guidance only; project gate rules belong in `evidence-policy.yaml`.

Recovery is bounded to two actions per run. Every model retry receives the previous attempt's structured validation errors, failed runtime checks, review blockers, or provider termination details. When code changes are present but only the structured response is invalid, muxdev freezes the isolated worktree and performs a read-only output correction before considering a full rollback and rerun. The Dashboard and Evidence Report expose the primary cause, attempted repairs, workspace safety, and an exact next command. Existing resume surfaces accept `auto`, `fix-output`, `retry`, and `switch-provider` actions.

Each Conversation resolves a fixed role team: planning, implementation, testing, and review, plus security review in Strict. `provider` names the primary implementation agent; optional `role_providers` overrides roles used by the selected workflow. Every Stage/Attempt has an independent worker identity, and only the writable implementation role may resume its session across revisions. Standard/Strict reviewers must use a Provider different from the implementer. This remains a trusted fixed DAG rather than an arbitrary agent swarm: writes are serial, while ready read-only stages on one frozen subject fan out to at most four workers and fan in deterministically.

Stage agents may raise structured clarification questions with two to four choices, a recommended choice, and optional free-form input. A low-risk, non-blocking question selects the disclosed recommendation after 60 seconds and reruns the current stage. Permission expansion, deletion or overwrite, credentials, network access, gate changes, and delivery acceptance never receive a timeout approval. The Conversation API exposes `interactions`, stage-completion `progress`, and per-stage `stage_deliveries`; the Dashboard separates standards, outputs, artifacts, and Evidence. Editing a stage standard creates a new Contract and records downstream impact without mutating a frozen Run or existing Evidence.

Non-zero Provider exits are classified as timeout, transient network/rate limiting, authentication, permission/sandbox, command configuration, process failure, or unknown failure. Only transient and unknown failures are retried automatically, and `switch-provider` is returned only when the frozen route contains a qualified fallback. Conversation `team`/`recovery` projections and SSE worker events expose the stage, Provider, exit code, redacted detail, attempts, remaining budget, workspace safety, and server-authorized next actions. Background start and recovery failures always return the Conversation to `needs_user` with a `run.failed_to_start` or `recovery.failed` event.

The optional `interop` extra adds the official MCP and ACP SDKs. `muxdev mcp serve --transport stdio` exposes the existing eight TaskService-backed control tools. Internally muxdev projects only the current Stage's exact read-only MCP tools into an isolated CLI/ACP configuration; the coding agent remains the native MCP client. ACP uses one local process and session per Stage, with permission requests checked against the frozen CapabilityGrant.

Provider protocol codecs keep semantic stage input uniform while preserving stdin/argument prompt transport and JSONL differences. Each worker receives a budgeted Context Pack of upstream typed facts, an AST repository map, and BM25-ranked history derived only from prior reports that still verify as `PASS`; retrieved memory never changes gate authority.

See the [measured simplification report](../../release-artifacts/simplification-report.md), [architecture diagrams](../../release-artifacts/architecture-diagrams.md), [open-source research record](../../release-artifacts/open-source-research.md), and [Evidence v3 examples](../../release-artifacts/evidence-v3-example.json).
