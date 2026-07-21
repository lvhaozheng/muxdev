# muxdev simplification report

## Measured outcome

| Metric | Before | After | Reduction |
|---|---:|---:|---:|
| Production Python lines | 38,933 | 7,042 | 81.9% |
| Python files | 166 | 63 | 62.0% |
| Workflows | 11 | 4 | 63.6% |
| Built-in Skills | 10 | 5 | 50.0% |
| SQLite tables | 54 | 12 | 77.8% |
| HTTP routes | 99 | 18 | 81.8% |
| CLI leaf commands | 159 | 30 | 81.1% |

## Deleted

- General-purpose LLM-writable Memory/RAG, multi-repository orchestration, arbitrary parallel-write DAG machinery, Skill marketplace governance, experimental learning projections, and the provider-score learning chain. A narrow derived-memory view was later reintroduced only for still-verifiable PASS deliveries.
- The 3,000-line Supervisor, 3,100-line Blackboard, dynamic repository facades, Provider sessions/planners, duplicate Evidence projections, legacy dashboards, and daemon-specific command surfaces.
- Markdown/TOML/Python gate authority in Skills. Legacy declarations are guidance-only and receive migration suggestions.

## Merged

- 11 workflows into `change / design / review / test`, with `lite / standard / strict` Profiles.
- 10 built-in Skills into five role guides; structured output schemas live only on workflow stages.
- Three Evidence products into `evidence-report.json`; DSSE is an optional envelope that binds the report digest.
- 54 database tables into 12 typed fact tables, with v7 import as unscored `legacy_evidence`.

## Kept because it carries technical depth

- Durable stage checkpoints, explicit human interrupts, safe reconciliation, deterministic DAG frontiers, read-only review fan-out, and bounded repair loops.
- Capability/certification-aware routing, Beta lower-bound history, heterogeneous review, Replay, and Benchmark.
- Deterministic EvidencePolicy evaluation, subject-bound Review, four-dimensional Scorecard, event-chain integrity, and DSSE attestation.
- Atomic backup/import/verification/switch migration with failure rollback.
- Provider-specific CLI protocol codecs and budgeted Context Packs with evidence-grounded BM25 retrieval.
