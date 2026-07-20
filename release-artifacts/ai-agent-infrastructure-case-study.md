# AI Agent Infrastructure Case Study: muxdev

## Problem

muxdev had accumulated platform features faster than it accumulated trustworthy delivery semantics. Eleven workflows, ten built-in Skills, 54 tables, 99 HTTP routes, 159 CLI commands, and three competing Evidence representations made completion hard to explain and harder to verify.

## Decision

The product was narrowed to a trusted-delivery control plane. TaskService owns lifecycle use cases; RunEngine owns durable stage execution. Provider output is treated as a claim, while diffs, command exit codes, subject digests, reviewer identity, human decisions, and event-chain hashes are runtime facts. Skills can guide work but cannot grant permissions or decide gates.

## Key engineering choices

1. A minimal persisted state machine instead of LangGraph, Temporal, or DBOS: four workflows did not justify a general orchestration dependency.
2. One EvidencePolicy and one deterministic Gate Engine: hard requirements remain independent from the explanatory Scorecard.
3. One Provider execution method: capability discovery and certification are separate from execution, and Provider self-reported confidence is ignored.
4. Append-only typed events and a 12-table fact store: projections no longer become competing sources of truth.
5. Subject-bound independent review and optional DSSE: attestations bind the final report without copying business facts.

## Result

Production Python fell from 38,933 to 4,328 lines (88.9% reduction) and files from 166 to 52 (68.7% reduction). The largest production file is 586 lines, no function exceeds 120 lines, all four-layer reverse-dependency checks pass, and the public surfaces are fixed at 29 CLI commands, 18 HTTP routes, and eight MCP tools.
