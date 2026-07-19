# Operations

[中文](../cn/operations.md)

<!-- section:daemon -->
## Daemon Lifecycle

The local daemon owns task writes and a bounded worker pool. Use `muxdev start`, `muxdev serve --status`, and `muxdev serve --restart`; use `muxdev doctor` after upgrades. Keep API and Dashboard on loopback unless an independently reviewed authentication boundary is added. A daemon restart must not require deleting runs or SQLite.

<!-- section:task -->
## Observe and Control Tasks

Use `muxdev tasks`, `status`, `story`, `diff`, `report`, `evidence`, `approvals`, and `actions`. Prefer JSON output for automation. A task in `awaiting_approval`, `awaiting_provider_action`, `awaiting_feedback`, `paused_budget`, or `blocked` is waiting by design; read its next action instead of repeatedly submitting duplicates.

<!-- section:recovery -->
## Recovery and Reconciliation

First capture status, execution attempts, latest error, lease state, and report. Safe, side-effect-free work may be requeued after lease expiry. An opaque attempt that may have written externally requires reconciliation. Use `continue`, `recover`, or controlled rollback only after the recorded reason is understood. Fencing rejects stale workers automatically; never work around it by editing rows.

<!-- section:backup -->
## Backup and Restore

Use the controlled storage backup API/CLI to create an archive, then verify its manifest and path safety. Restore only into an empty, explicit target and verify schema/migration checksums before switching. The v7 database and run artifact layout are compatibility boundaries. Never restore an archive containing path traversal or a database from a newer unsupported schema.

<!-- section:benchmark -->
## Benchmark and Replay

Replay benchmark fixtures are registered and hash-bound. They are deterministic, side-effect-free regression tools and must be labeled simulation. Live benchmark execution needs explicit acknowledgement, an allowlist, and a cost cap; do not feed live results into production learning unless mode, evidence, and completeness checks pass.

<!-- section:diagnostics -->
## Diagnostics Checklist

1. Run `ruff check .` and `python scripts/verify_docs.py` for repository health.
2. Run targeted tests for the failed area, then `pytest -q -m "not release"`.
3. Inspect daemon logs, task trace, provider transcript, contract validation, and Evidence evaluation.
4. Verify free disk space, Git worktree support, API ports, current Provider fingerprint, and signing-key permission status.
5. Before cleanup, preserve a verified backup and the task report.

Security incident handling is in [Security and trust](security-and-trust.md).
