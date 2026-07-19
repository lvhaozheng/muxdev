# Security Policy

Security fixes target the current release-candidate line.

Read the complete policy in [English](docs/en/security-and-trust.md) or [中文](docs/cn/security-and-trust.md).

Report issues through the repository's private security channel when available. Include the muxdev version, platform, redacted reproduction, affected Provider fingerprint, and relevant event or attestation hashes. Never disclose credentials, signing private keys, Provider state, prompts, transcripts, private repository contents, or uncontrolled archives.

muxdev is local-first and assumes one OS user controls the machine. Loopback authentication does not defend against a malicious process already running as that user. An unpinned local attestation identity is self-asserted, not an organization identity.
