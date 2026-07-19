# ADR 0008: Dual-Track Benchmark And Local Release Candidate

## Decision

muxdev separates deterministic Replay validation from cost-bearing live
Provider measurement. Replay is mandatory in CI and can never produce a public
quality claim. Live execution is CLI-only, budget gated and reuses one
Provider/Case outcome matrix across all route strategies.

The local API uses a private bearer token and browser bootstrap cookie. The
release target is `0.2.0rc1` artifacts and verification metadata; external
publication remains an explicit operator action outside this increment.

## Consequences

- CI is deterministic and credential free.
- Provider cost and external side effects remain explicit.
- A live-gate failure remains visible instead of being replaced by synthetic
  percentages.
- Existing unauthenticated local API clients must read the daemon token or use
  the browser bootstrap flow.
