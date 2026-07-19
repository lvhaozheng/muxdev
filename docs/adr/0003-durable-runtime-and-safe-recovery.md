# ADR 0003: Durable Runtime And Safe Recovery

- Status: Accepted
- Date: 2026-07-19

## Context

The previous daemon launched one transient thread per request. Its process-local
map prevented common duplicates but could not survive restart, apply backpressure,
or distinguish a requested cancellation from an acknowledged stop. Retrying an
opaque Provider after a crash can repeat an external side effect.

## Decision

Use the Blackboard SQLite database for a persistent at-least-once execution
queue. Each start/resume invocation is a Job with an expiring lease, random lease
token, monotonic fencing token, and immutable Execution Events. RunSpec and the
initial Job commit atomically with Run creation.

Run a fixed two-thread pool by default. Heartbeat active leases every five
seconds against a 30-second lease. Validate the execution fence before every
Runtime database commit. Retry only classified infrastructure failures, at most
three times with 1/5/30-second backoff.

Use cooperative cancellation followed by controlled process-tree termination.
Keep public Run statuses stable: execution state exposes `cancel_requested`, and
the Run becomes `aborted` only after acknowledgement.

On restart, automatically recover only when no opaque Provider attempt is in
flight. Otherwise require an explicit, audited retry or abort decision.

## Consequences

- A daemon crash no longer loses queued work.
- Bounded concurrency provides deterministic local resource use.
- Stale Workers cannot commit delivery state after losing a lease.
- Execution history explains retries and recovery separately from delivery
  Evidence.
- External Provider effects remain at-least-once and may need human
  reconciliation.
- Full process isolation and Provider-specific cancellation remain later work.
