# Changelog

## 0.2.0rc1

- Narrowed muxdev from a general multi-Agent platform to a trusted-delivery control plane.
- Replaced competing gate sources with EvidencePolicy, five typed EvidenceRecord variants, deterministic GateDecision, and a four-dimension explanatory Scorecard.
- Consolidated the product to four workflows, three Profiles, five built-in Skills, one Provider execution method, 12 SQLite tables, 29 CLI commands, 18 HTTP routes, and eight MCP tools.
- Added Runtime-replayed command evidence, subject-bound independent review, event-chain prefix binding, and optional in-toto-style DSSE export.
- Added durable stage resume, explicit human interactions, bounded change repair, capability/certification-aware routing, Beta lower-bound history, Replay, and Benchmark.
- Added backup/import/verify/atomic-switch v7 migration. Historical Evidence v2 is retained only as unscored legacy events.
- Removed Memory/RAG, multi-repository orchestration, generic DAG/parallel machinery, Provider planning/session abstractions, daemon clients, legacy dashboards, and duplicate Evidence projections.

This release intentionally breaks legacy CLI and HTTP paths. Existing v7 data remains safely readable through migration; real Provider quality claims still require certified live outcomes.
