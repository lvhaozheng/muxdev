# ADR 0004: Certified Adapters And Tiered Isolation

- Status: Accepted
- Date: 2026-07-19

## Context

Provider name and help text do not prove that a CLI emits stable events,
reports usage accurately, honors cancellation, or enforces a sandbox. Treating
such declarations as policy capabilities would let an opaque external process
cross a high-risk delivery boundary without evidence.

A single maximum-isolation mode would also make ordinary local tasks expensive
and brittle, while process-only execution is insufficient for sensitive auth,
payment, secret, permission, migration, and security work.

## Decision

Adopt a versioned `AgentHarnessAdapter` lifecycle and a four-state capability
model. Only `verified` capabilities satisfy policy; CLI-declared features remain
`advertised`. Preserve unknown Provider events without semantic invention.

Certify Codex and Qwen with provider-specific decoders and offline/live reports,
but keep both at opaque trust. Mark deterministic Mock and hash-verified Replay
as managed. Retain Generic CLI only as uncertified, opaque, and experimental.

Run offline certification automatically without model calls. Require explicit
`--live --yes` and a positive budget for real-provider certification. Bind every
report to Provider/executable/help/platform/Adapter/policy fingerprints and
invalidate it on change.

Persist risk and isolation requirements in RunSpec v2. Require offline
certification and native Provider sandboxing for ordinary real-provider tasks.
Require live certification, Provider sandboxing, and cooperative cancellation
for high-risk real-provider tasks.

When high-risk isolation is insufficient, pause before Provider launch and
create a subject-bound `isolation_downgrade` Approval. Never downgrade
automatically. Invalidate the waiver when its Provider, certification, policy,
capability gap, isolation, or Attempt generation changes.

Store certifications and hash-chained Harness Events in Blackboard schema v4.
Keep Run State Events, Execution Events, Harness Events, and Evidence as four
separate fact types.

## Consequences

- Policy decisions use verified behavior rather than provider branding.
- Codex/Qwen remain honest opaque dependencies even after successful
  certification.
- Ordinary tasks retain a practical native-sandbox path.
- High-risk shortfalls are visible, auditable, and must be consciously waived.
- Replay enables deterministic demos without masquerading as production work.
- Fingerprint changes deliberately add re-certification friction.
- Provider external effects remain at-least-once and opaque crash recovery
  still requires third-week reconciliation.
- Capability-constrained routing is implemented by ADR 0005; signed Delivery
  Attestations remain Week 6 work.
