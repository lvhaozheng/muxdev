# ADR 0005: Quality-First Routing And Heterogeneous Read-Only Review

- Status: Accepted
- Date: 2026-07-19

## Context

The previous runtime could route stages from role configuration and aggregate
historical Provider scores. Those inputs did not prove that a Provider met the
task's certified capabilities, isolation, production boundary, or budget, and
could not explain a stable task-level decision. A fixed multi-role pipeline
also increased cost and coordination without guaranteeing independent review.

Quality history is useful only when its result is tied to complete delivery
evidence. Simulation/replay results must not improve production claims, sparse
samples must not produce overconfident selection, and route failures must not
trigger silent opaque Provider replay.

Sensitive changes benefit from independent review, but assigning another role
to the same Provider or executable is not heterogeneous. A Reviewer that can
write to the worktree is also not a valid policy control.

## Decision

Adopt one immutable RouteDecision per Task Unit. Filter candidates by current
Adapter certification, verified capabilities, isolation, delivery mode,
allowlist, and budget before comparing quality. Use a Jeffreys-prior Beta
posterior lower bound over evidence-complete, task-class- and mode-matched
outcomes. Prefer an explicit certified fallback while evidence is sparse and
use deterministic tie breaks.

Persist bounded, source-free TaskFeatureSet metadata and the complete candidate
snapshot in Blackboard schema v5. Persist the routing policy in RunSpec v3.
Resume reuses the original decision. Offline replay compares the stored facts
without probing or invoking a Provider and is never production evidence.

For high-risk or explicitly required review, choose a different Provider with
a distinct known fingerprint and `read_only=verified`. Freeze hash-only review
inputs, blind the main Provider identity, enforce pre/post worktree hashes, and
require a structured verdict. Prevent Run completion while a required review
is incomplete.

If no valid Reviewer exists, pause before Provider launch and require a
`heterogeneous_review_unavailable` Approval bound to the decision, policy,
feature hash, and candidate exclusions. Never auto-waive. Run isolation
preflight before this reviewer preflight.

Register a 24-case, six-class trusted-routing benchmark manifest, but separate
plan registration from paid live execution. Require acknowledgement and a
positive budget for a live plan; do not publish improvement claims until an
executed, reproducible snapshot exists.

## Consequences

- A route is explainable through explicit eligibility and reason codes.
- Certification and isolation are gates, not soft score features.
- Mock/Replay evidence cannot influence production estimates.
- Sparse data remains conservative and deterministic.
- The main Agent cannot silently change after an opaque failure.
- High-risk independent review is real heterogeneity or an explicit waiver.
- Codex/Qwen remain ineligible as read-only Reviewers until that capability is
  verified, even when certified for main-Agent execution.
- Route and review facts add schema and product surface complexity.
- The current benchmark supports validation and planning, not measured public
  quality claims.
- Signed RouteDecision and review binding remains Week 6 work.

