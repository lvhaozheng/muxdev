# `.muxattest` Format And Offline Verification

## Format

`muxdev.attestation-bundle.v1` is a deterministic ZIP archive. Members are
ordered lexicographically, have a fixed 1980 timestamp, regular-file mode, and
stable compression settings. `manifest.json` declares every other member with
its path, uncompressed size, media type, and SHA-256.

Required signed content:

```text
manifest.json
attestation/payload.json
attestation/signature.json
attestation/public-key.pem       # signed records only
evidence/events.jsonl
evidence/manifest.json
evidence/evaluation.json
projections/delivery.json
```

Allowlisted artifacts are content-addressed under:

```text
artifacts/{sha256-hex}/{basename}
```

The manifest maps each member back to a relative source such as `diff.patch`,
the privacy-minimal signed report, delivery contract, validator panel, or
bounded test/review report. The caller cannot add paths.

## Exclusions

The format excludes Provider state, private keys, credentials, environment
variables, task/Prompt text, transcript/message deltas, raw stdout/stderr,
session capsules, the full local report, full worktree, absolute paths,
symlinks, and arbitrary files. The privacy-minimal report contains identifiers,
hashes, and signature status rather than local task or path content.

## Parser Limits

An implementation must reject:

- more than 1,024 members;
- any member larger than 64 MiB;
- more than 256 MiB total uncompressed data;
- an absolute path, Windows drive path, backslash, empty segment, or `..`;
- duplicate member or declaration names;
- symbolic links and non-regular archive tricks;
- an undeclared or missing member;
- abnormal compression ratio above the configured safety bound;
- member size/hash, manifest payload hash, Evidence hash/head, or signature
  mismatch.

muxdev reads members with bounded streams and never extracts them into the
workspace during verification.

## Offline Verification

No database, Daemon, Provider executable, network, project checkout, or private
key is required:

```powershell
muxdev attestation verify delivery.muxattest --json
```

Expected identity status is `self_asserted`. To bind the signer to a fingerprint
received through a separate trusted channel:

```powershell
muxdev attestation verify delivery.muxattest `
  --trusted-fingerprint sha256:... `
  --require-trusted
```

The second command succeeds only when the Ed25519 signature, Evidence, bundle
manifest, and pinned identity all match. The bundle public key alone never
produces `trusted`.
