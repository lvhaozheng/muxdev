# Security Policy

Security fixes target the current release line.

Report issues through the repository's private security channel when available. Include the muxdev version, platform, redacted reproduction, affected Provider fingerprint, and relevant event or attestation hashes. Never disclose credentials, signing private keys, Provider state, prompts, transcripts, private repository contents, or uncontrolled archives.

muxdev is local-first and assumes one OS user controls the machine. Binding HTTP beyond loopback changes that threat model and requires an external authentication boundary. An unsigned DSSE envelope provides digest binding but no identity; a locally generated, unpinned signing key is self-asserted rather than an organization identity.

Runtime command checks use argv execution without a shell, but an authorized test stage can still execute repository code. Run untrusted repositories inside an OS sandbox or disposable machine. Skills cannot expand workflow permissions, and Provider-reported Evidence, confidence, or gate decisions are never trusted as runtime facts.
