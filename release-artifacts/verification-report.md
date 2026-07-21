# muxdev verification report

Measured on Windows with Python 3.13 after the bounded Supervisor/Context optimization.

| Verification | Result | Measured time |
|---|---:|---:|
| Full Pytest suite (workspace sandbox) | 40 passed, 4 platform/sandbox skips | 66.27s |
| Official MCP + ACP stdio conformance outside the Windows pipe sandbox | 3 passed | 8.11s |
| Ruff over `src/muxdev`, `scripts`, and `tests` | passed | <2s combined check run |
| Python bytecode compilation | passed | <2s combined check run |
| Documentation links/layout | passed | <2s combined check run |
| Git whitespace validation | passed | <2s combined check run |

## Current architecture facts

| Metric | Value |
|---|---:|
| Production Python logical lines | 7,042 |
| Production Python files | 63 |
| Largest production file | 996 lines |
| Functions over 120 lines | 0 |
| Layer cycles | 0 |
| Reverse four-layer dependency violations | 0 |
| CLI / HTTP / MCP surfaces | 30 / 18 / 8 |
| SQLite core tables | 12 |

## Behavior covered

- Normal lite `PASS`, artifact tamper detection, and event-chain tamper detection.
- Standard self-review block and strict pause → frozen Policy → approve → resume.
- Strict ordinary/security review fan-out events and separate `security_review` Requirement.
- Provider-forged test success and argv are ignored; only frozen policy-owned VerificationCommands produce ExecutedCheck evidence.
- Read-only Reviewer write violation detected from the changed Subject and never applied to the main workspace.
- A Runtime verification command that exits zero but modifies the Subject is marked integrity-invalid and never applied.
- Explicit stdin versus argument Prompt transport and Provider-specific Codex/Claude JSONL normalization.
- Deterministic DAG Frontier grouping for ordinary/security Review.
- Budgeted Context Pack with upstream structured facts.
- Evidence-grounded Memory promotion only for reports that still verify as `PASS`; a tampered report is excluded.
- Skill permission escalation, legacy Skill gate migration warnings, Skill lock drift, DSSE binding, v7 migration, rollback on failed migration, HTTP/MCP read surfaces, and public interface budgets.
- Immutable RunPolicySnapshot resume behavior, exact read-only MCP grants, Secret minimization, Provider fingerprint invalidation, ProcessSupervisor timeout cleanup, and conflict-atomic ChangeSets with whole-file payload artifacts.
- Official Control MCP input/output Schema, ACP initialize/session/streaming, in-Grant permission, protocol cancel, and ACP-to-MCP read-only fixture transfer.
- The simulated login report is Pydantic-valid Evidence v3 with a reproducible `PASS` decision and eight typed records.
- The Chinese teaching pack verifier confirms 130 unique interview question families across 13 domains, complete Q1-Q22 coverage, the simulated login evidence, four mandatory honesty boundaries, and every referenced core code symbol.

The host Conda environment emits a `requests` dependency compatibility warning. muxdev does not depend on or import `requests`; Ruff, compilation, and all test layers pass despite that unrelated host warning. A real external ACP Agent is not installed on this host, so release-grade live certification remains an explicit pre-release gate rather than a claimed result.
