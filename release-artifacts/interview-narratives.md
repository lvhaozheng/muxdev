# Interview narratives

## 30 seconds

I turned muxdev from a feature-heavy multi-Agent platform into a local Trusted Agent Harness. Each run freezes policy, provider fingerprint, Skills, capabilities, verification commands, and the workspace manifest; only runtime-executed checks and conflict-safe ChangeSets can pass the gate. MCP remains native to the coding CLI while muxdev projects an exact read-only tool set, and ACP makes the Agent replaceable. The result keeps one EvidencePolicy, 12 fact tables, and fixed 30/18/8 CLI, HTTP, and MCP surfaces.

## 3 minutes

The original system had real depth, but the depth was obscured by breadth: 11 workflows, 10 Skills, 54 tables, and multiple ways to decide whether a delivery passed. I first froze machine-readable metrics and characterization data. Then I made five decisions. First, only TaskService and RunEngine remain as core lifecycle services. Second, the Provider boundary is one execute method; the runtime captures diffs, exit codes, usage, interactions, and identities. Third, Skills are prompt guidance and cannot define a gate. Fourth, EvidencePolicy produces deterministic PASS, BLOCKED, or WAITING_HUMAN while a four-part Scorecard only explains quality. Fifth, the store is an append-only fact model with 12 tables and atomic v7 migration.

I kept the hard parts that are interview-worthy: crash-safe stage checkpoints, safe handling of opaque interrupted providers, deterministic DAG frontiers, parallel read-only review with write-set detection, bounded repair loops, Beta-lower-bound routing history, heterogeneous reviewers, subject digest binding, event-chain verification, and DSSE export. I deliberately did not add LangGraph or Temporal because four static workflows and three Profiles did not justify a second general checkpoint runtime. The result is 7,042 production lines, 63 files, no cycle, no function over 120 lines, and a high-score-but-BLOCKED example that proves score cannot waive a required approval.

## 10 minutes

Start with the trust boundary: a model may suggest a command or claim it tested code, but muxdev executes only policy-owned argv arrays frozen before Provider startup. `ExecutedCheck`, working-directory digest, actual exit code, bounded output artifacts, and the resulting ChangeSet are runtime facts. Gate evaluation is deterministic and fail-closed: missing required evidence, failed checks, digest mismatch, wrong review target, self-review, unresolved high findings, pending/rejected approval, malformed Skill output, or attempted Provider gate decisions block delivery.

Then explain orchestration and durability: each execution is a persisted job; workflow validation computes deterministic DAG frontiers. The strict change workflow fans ordinary and security review out over one frozen read-only subject, invokes Providers concurrently, and commits results in fixed order. Resume skips completed stages, waits on explicit interactions, and refuses to replay an interrupted opaque Provider stage without reconciliation. A maximum two-round fix/test/review loop prevents unbounded chatter.

For context, each worker receives a 12k-character pack of upstream typed facts, an AST repo map, and BM25-ranked memories derived only from Evidence Reports that still verify as PASS. The pack records source run IDs, truncation, and a digest; memory can influence a prompt but never a gate.

For routing, candidates are capability/certification filtered. Only PASS outcomes with valid integrity and runtime-confirmed independent review enter Beta history; Provider confidence never does. Cost and latency p90 are combined with the conservative quality bound, and standard/strict select a heterogeneous reviewer when one is certified.

Finally explain migration and attestation: v7 databases are backed up, imported into a temporary 12-table database, count/digest verified, and atomically switched. v2 Evidence becomes unscored legacy events. The canonical product is one evidence-report; DSSE wraps an in-toto-style Statement referencing its digest. This preserves audit depth while removing duplicated facts.
