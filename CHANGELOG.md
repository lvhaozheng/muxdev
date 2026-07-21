# Changelog

## Unreleased

- Added Conversation-level fixed role teams with optional `role_providers`, independent Stage/Attempt worker identities, pre-bound Run IDs, live idempotent Worker/fan-out SSE projection, and a maximum of four read-only workers while preserving serial writes and deterministic fan-in.
- Added classified Provider failures, bounded redacted diagnostics, fallback-aware recovery actions, strict server-side recovery-action validation, stable background failure events, and Dashboard Agent roster/failure cards with accessible live status.
- Added Trusted Agent Harness v1: immutable RunPolicySnapshot, frozen CapabilityGrant, Runtime-owned VerificationCommand/ExecutedCheck evidence, unified process-tree cancellation, conflict-safe whole-file ChangeSet artifacts, and fingerprint-bound live Provider certification.
- Added optional official MCP/ACP interoperability without a generic gateway: eight typed TaskService-backed Control MCP tools, isolated Stage-level read-only MCP projection, one local ACP session per Stage, permission enforcement, normalized ProviderEvents, and configured/observed/verified reporting.
- Added separate base/interop CI coverage and local ACP→MCP conformance fixtures. Production release remains blocked until a real external ACP Agent completes live certification.
- Added a Chinese mentor teaching playbook with contribution-integrity rules, a ten-unit curriculum, real engineering difficulty matrix, answer protocol, grading rubric, and completion gates.
- Added a grounded 130-question interview drill bank across 13 domains plus an automated verifier that checks question coverage, Q1-Q22 completeness, honesty boundaries, login evidence, and current code symbols.
- Added bounded self-recovery with prior-attempt feedback, read-only structured-output correction, isolated attempt checkpoints, transient Provider retry/fallback, preflight independent-review checks, and actionable Dashboard/Evidence diagnostics.

- Added Provider protocol codecs that keep `StageExecutionInput` uniform while validating stdin/argument Prompt transport and decoding Codex/Claude JSONL with Provider-specific rules.
- Added deterministic DAG execution waves and bounded read-only fan-out/fan-in for ordinary and security review, including frozen Subject binding, ordered commits, and write-set violation detection.
- Added budgeted Context Packs containing upstream typed facts, an AST repository map, and BM25-ranked history derived only from Evidence Reports that still verify as `PASS`.
- Added a separate strict `security_review` Evidence Requirement, a schema-valid simulated login Evidence Report, and a Q1-Q22 Chinese interview deep dive.
- Provider-internal Action pause/resume and arbitrary Stage rollback remain explicitly out of scope for this increment.

## 0.2.0rc1

- Narrowed muxdev from a general multi-Agent platform to a trusted-delivery control plane.
- Replaced competing gate sources with EvidencePolicy, five typed EvidenceRecord variants, deterministic GateDecision, and a four-dimension explanatory Scorecard.
- Consolidated the product to four workflows, three Profiles, five built-in Skills, one Provider execution method, 12 SQLite tables, 29 CLI commands, 18 HTTP routes, and eight MCP tools.
- Added Runtime-replayed command evidence, subject-bound independent review, event-chain prefix binding, and optional in-toto-style DSSE export.
- Added durable stage resume, explicit human interactions, bounded change repair, capability/certification-aware routing, Beta lower-bound history, Replay, and Benchmark.
- Added backup/import/verify/atomic-switch v7 migration. Historical Evidence v2 is retained only as unscored legacy events.
- Removed general-purpose Memory/RAG, multi-repository orchestration, generic DAG/parallel-write machinery, Provider planning/session abstractions, daemon clients, legacy dashboards, and duplicate Evidence projections.

This release intentionally breaks legacy CLI and HTTP paths. Existing v7 data remains safely readable through migration; real Provider quality claims still require certified live outcomes.
