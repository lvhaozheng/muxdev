# ADR 0006: Per-Project Signing And Offline Trust

- Status: Accepted
- Date: 2026-07-19

## Context

Weeks 2–5 created several independently useful facts: lifecycle State Events,
durable Execution Events, certified Harness Events, immutable RouteDecisions,
heterogeneous review results, subject-bound Approvals, and Evidence v2. A local
administrator could still replace all of those files and recompute an unsigned
hash chain. Evidence also used local paths that were not portable to an offline
recipient.

The product needs a demonstrable delivery boundary without claiming an
organization PKI that does not exist. It must work on one developer machine,
remain useful in interviews and offline demos, avoid sending source or
credentials to a service, and fail closed where a false production claim would
be dangerous.

## Decision

Use one independent Ed25519 identity per project. Persist only the project UUID
and public identity in `.muxdev/trust`; store private keys under daemon-private
`MUXDEV_HOME/data/signing/projects`. Auto-generate the first key, refuse silent
replacement after private-key loss, and make rotation explicit, reasoned,
double-signed, and destructive for the old private key.

Adopt `muxdev.delivery-attestation.v1` over a strict canonical JSON encoding.
Bind repository baseline/final state, route, Adapter certification, isolation,
review, Approval subjects, Provider Attempts, test projections, Evidence, and
event heads. Use a detached Ed25519 envelope carrying an SPKI public key and
fingerprint.

Add `attesting` and commit the completed State Event and immutable attestation
generation in one SQLite transaction. Materialized files are caches; the
database record is authoritative. All new Runs attempt signing. High-risk Runs
fail closed. Ordinary Runs may complete with an explicit unsigned record for
compatibility, but unsigned production results cannot update routing quality.

Export a deterministic, allowlist-only `muxdev.attestation-bundle.v1` ZIP. It
contains no private key, credentials, environment values, task/Prompt text,
transcript, Provider raw output, session capsule, arbitrary caller file, full
worktree, absolute path, or symlink. Verify it using streaming bounded reads
without a database, network, Provider, or extraction into the project.

Treat a bundle-carried public key as `self_asserted`. Only an independently
supplied matching fingerprint upgrades identity to `trusted`. A wrong pin is a
hard verification failure.

## Consequences

- An offline recipient can detect payload, Evidence, route, approval, patch,
  test, or bundle tampering.
- One transaction prevents a Run from being completed without its required
  proof, or vice versa.
- Project identities remain independent and survive directory moves.
- A clone intentionally receives a new identity unless trust metadata is
  transferred through an explicit future process.
- Ordinary unsigned completion remains compatible but visibly untrusted and is
  excluded from production routing learning.
- Private keys and Provider credentials are physically separate from projects,
  worktrees, backups, and exports.
- Self-signing does not establish real-world identity, key revocation, or
  trustworthy time. Host/key compromise can forge later attestations.
- Remote CA/HSM, Sigstore, transparency, team key distribution, and Provider
  exactly-once behavior remain outside v0.2 Week 6.
