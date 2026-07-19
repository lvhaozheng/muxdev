# ADR 0002: Event-Driven Lifecycle State And Reliable SQLite

- Status: Accepted and implemented
- Date: 2026-07-18
- Decision owners: muxdev maintainers

## Context

muxdev needs production-grade single-machine state without turning every
feature table into an event-sourced aggregate. The prior databases disabled
journaling, committed each CRUD call independently, and used schema inspection
as an implicit migration mechanism. Evidence events existed, but using delivery
evidence as mutable runtime state would conflate two trust boundaries.

## Decision

1. Event-source only the core Run, Stage, Approval, and Provider Action
   lifecycle. Other feature records remain transactional CRUD projections.
2. Make `state_events` the operational lifecycle source and retain Evidence v2
   as a separate delivery-trust projection.
3. Validate every lifecycle event with a pure reducer and atomically append the
   event, advance the aggregate version, and update SQLite projections.
4. Use one standard-library SQLite engine for Blackboard and MemoryStore, with
   WAL/FULL defaults, explicit units of work, read-only inspection, and numbered
   checksummed migrations.
5. Provide online backup, offline verified restore, and read-only replay. HTTP
   may create and verify controlled backups but cannot restore data.
6. Preserve public status strings and temporary Blackboard compatibility
   methods while moving Runtime and Daemon code to `LifecycleService`.

## Consequences

- Crash and concurrency behavior becomes testable at transaction boundaries.
- New Runs can be replayed; legacy history is honestly labeled incomplete.
- The append-only event table adds storage and migration complexity, but avoids
  the much larger risk of full-system event sourcing in one week.
- File publication still requires a protocol because SQLite cannot transact
  with the filesystem. The chosen protocol can leave a hashed orphan, never a
  false database claim about a missing file.
- Local idempotency does not imply exactly-once Provider execution. Durable
  scheduling and remote-effect reconciliation remain a later decision.

## Rejected Alternatives

- **Audit-only events:** would preserve split commits and could not rebuild or
  validate lifecycle projections.
- **Full event sourcing:** would force unrelated memory, evidence, cache, and
  ecosystem tables through a high-risk migration with little product value.
- **Alembic or a server database:** unnecessary dependencies for the current
  local-first, single-machine production boundary.
- **HTTP restore:** expands the local API attack surface and makes process/lock
  coordination harder to prove.

