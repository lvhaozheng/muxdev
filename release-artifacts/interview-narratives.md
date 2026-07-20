# Interview narratives

## 30 seconds

I turned muxdev from a feature-heavy multi-Agent platform into a trusted-delivery control plane. The key was separating Provider claims from runtime facts and separating hard gates from evidence-quality scores. I removed Memory/RAG and several shadow abstractions, introduced one EvidencePolicy, a 12-table event-based store, subject-bound independent review, durable resume, and optional DSSE. The measured result was 88.9% fewer production lines, 68.7% fewer Python files, and fixed 29/18/8 CLI, HTTP, and MCP surfaces.

## 3 minutes

The original system had real depth, but the depth was obscured by breadth: 11 workflows, 10 Skills, 54 tables, and multiple ways to decide whether a delivery passed. I first froze machine-readable metrics and characterization data. Then I made five decisions. First, only TaskService and RunEngine remain as core lifecycle services. Second, the Provider boundary is one execute method; the runtime captures diffs, exit codes, usage, interactions, and identities. Third, Skills are prompt guidance and cannot define a gate. Fourth, EvidencePolicy produces deterministic PASS, BLOCKED, or WAITING_HUMAN while a four-part Scorecard only explains quality. Fifth, the store is an append-only fact model with 12 tables and atomic v7 migration.

I kept the hard parts that are interview-worthy: crash-safe stage checkpoints, safe handling of opaque interrupted providers, bounded repair loops, Beta-lower-bound routing history, heterogeneous reviewers, subject digest binding, event-chain verification, and DSSE export. I deliberately did not add LangGraph or Temporal because four static workflows and three Profiles did not justify a general DAG runtime. The result is 4,328 production lines, 52 files, no cycle, no function over 120 lines, and a high-score-but-BLOCKED example that proves score cannot waive a required approval.

## 10 minutes

Start with the trust boundary: a model may claim it tested code, but only the runtime sees the command, working directory, exit code, stdout/stderr digests, and resulting subject. That led to five EvidenceRecord types and one frozen EvidencePolicy. Gate evaluation is deterministic and fail-closed: missing required evidence, failed checks, digest mismatch, wrong review target, self-review, unresolved high findings, pending/rejected approval, malformed Skill output, or attempted Provider gate decisions block delivery. The Scorecard uses transparent numerators and denominators; zero denominators are N/A.

Then explain durability: each execution is a persisted job; each stage transition and evidence record is appended to a hash chain. Resume skips completed stages, waits on explicit interactions, and refuses to replay an interrupted opaque Provider stage without reconciliation. A maximum two-round fix/test/review loop is built into the fixed change workflow. This is enough for the product and easier to prove than a general workflow engine.

For routing, candidates are capability/certification filtered. Only PASS outcomes with valid integrity and runtime-confirmed independent review enter Beta history; Provider confidence never does. Cost and latency p90 are combined with the conservative quality bound, and standard/strict select a heterogeneous reviewer when one is certified.

Finally explain migration and attestation: v7 databases are backed up, imported into a temporary 12-table database, count/digest verified, and atomically switched. v2 Evidence becomes unscored legacy events. The canonical product is one evidence-report; DSSE wraps an in-toto-style Statement referencing its digest. This preserves audit depth while removing duplicated facts.
