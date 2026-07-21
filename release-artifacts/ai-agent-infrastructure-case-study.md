# AI Agent Infrastructure Case Study: muxdev

## Problem

muxdev had accumulated platform features faster than it accumulated trustworthy delivery semantics. Eleven workflows, ten built-in Skills, 54 tables, 99 HTTP routes, 159 CLI commands, and three competing Evidence representations made completion hard to explain and harder to verify.

## Decision

The product was narrowed to a trusted-delivery control plane. TaskService owns lifecycle use cases; RunEngine owns durable stage execution. Provider output is treated as a claim, while diffs, command exit codes, subject digests, reviewer identity, human decisions, and event-chain hashes are runtime facts. Skills can guide work but cannot grant permissions or decide gates.

## Key engineering choices

1. A minimal persisted Supervisor DAG instead of LangGraph, Temporal, or DBOS: four workflows use deterministic frontiers, bounded read-only fan-out, and at most two repair rounds without a second checkpoint store.
2. One EvidencePolicy and one deterministic Gate Engine: hard requirements remain independent from the explanatory Scorecard.
3. One Provider execution method plus protocol codecs: semantic inputs are uniform while stdin/argv transport and Codex/Claude JSONL decoding remain Provider-specific.
4. Append-only typed events and a 12-table fact store: projections no longer become competing sources of truth.
5. Evidence-grounded Context Packs: upstream structured facts, a deterministic repo map, and BM25-ranked history from still-verifiable PASS reports share one auditable budget.
6. Subject-bound independent and security review plus optional DSSE: attestations bind the final report without copying business facts.

## Result

Production Python fell from 38,933 to 7,042 lines (81.9% reduction) and files from 166 to 63 (62.0% reduction). The largest production file is 996 lines, no function exceeds 120 lines, all four-layer reverse-dependency checks pass, and the public surfaces are fixed at 30 CLI commands, 18 HTTP routes, and eight MCP tools.
