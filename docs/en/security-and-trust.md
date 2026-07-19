# Security and Trust

[中文](../cn/security-and-trust.md)

<!-- section:boundary -->
## Trust Boundary

muxdev is local-first and assumes one OS user controls the machine. Loopback authentication reduces accidental browser/API access but does not protect against a malicious process already running as that user. Provider CLIs, model output, workspace files, Skills, retrieved text, and restored archives are untrusted inputs. Use a separate OS account or sandbox for a stronger boundary.

<!-- section:threats -->
## Main Threats

- Prompt injection causing unauthorized tools or writes.
- A Provider claiming tests/review succeeded without a valid contract.
- Concurrent workers overwriting changes or stale workers committing.
- Approval reuse after the plan, command, fingerprint, or artifact subject changes.
- Secrets leaking into prompts, transcripts, artifacts, logs, or bundles.
- Tampered SQLite migrations, backup archives, Evidence, or attestations.
- Self-asserted local signing identity being mistaken for organization identity.

<!-- section:approvals -->
## Approvals and Human Control

Policy approvals bind a canonical subject hash and are rechecked before use. Plan, write, shell, merge, external access, isolation downgrade, and reviewer waiver can require separate decisions. Provider Actions are not muxdev approvals and are never auto-confirmed. Denial, missing response, or subject drift leaves the task paused or blocked.

<!-- section:isolation -->
## Isolation and Least Privilege

Each Run uses a task worktree; parallel writers use separate worker workspaces. Read-only stages are checked for filesystem mutation. Capability routing filters by certified sandbox/read-only requirements before quality or cost. High-risk review should use a fingerprint-distinct Provider. Network, installation, and external effects require explicit scope.

<!-- section:evidence -->
## Evidence and Fail-Closed Validation

Test and Review results require structured contracts. Evidence records claims, commands, exit codes, artifacts, strengths, gaps, and risks. Missing or inconsistent results block the gate. Ledger and event hashes reveal modification but cannot make a false observation true; independent review and reproducible commands remain necessary.

<!-- section:signing -->
## Signing and Attestation

Project Ed25519 keys live outside the project. Completion can create a signed, canonical payload binding route, isolation, review, approvals, tests, Evidence, and allowlisted artifact hashes. `.muxattest` bundles exclude private keys and prompt material. Verify integrity, evidence status, identity pinning, and warnings. An unpinned key is self-asserted.

<!-- section:secrets -->
## Secrets and Private Data

Never place tokens or private keys in task text. Provider subprocess environments are allowlisted; redaction is defense in depth, not permission to ingest secrets. Restrict permissions on muxdev home, Provider state, transcripts, backups, and signing keys. Public bug reports must use redacted reproductions and hashes, not private code or full transcripts.

<!-- section:reporting -->
## Reporting Security Issues

Use the repository’s private security-reporting channel when available. Include muxdev version, OS, redacted steps, expected/actual boundary, affected Provider and fingerprint, and relevant event/attestation hashes. Do not attach credentials, private repository contents, signing keys, Provider state, or uncontrolled archives. Root [SECURITY.md](../../SECURITY.md) is the short policy entry.
