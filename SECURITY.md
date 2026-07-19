# Security Policy

## Supported Version

Security fixes target the current `0.2.0rc1` line during the release-candidate
period.

## Reporting

Do not include credentials, private keys, Provider state, prompts, transcripts
or private repository contents in a public report. Provide the muxdev version,
platform, redacted reproduction steps and relevant event or attestation hashes
through the repository's private security-reporting channel when available.

## Local Trust Boundary

muxdev is single-machine and single-operator. The bearer token, signing keys and
Provider state are private to the current OS user. A process already running as
that user may still access local files; use a separate OS account for a stronger
boundary. An unpinned `.muxattest` identity is self-asserted, not an organization
identity.
