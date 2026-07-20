# muxdev English reference

muxdev is a local-first trusted-delivery control plane for AI agents, not a general multi-agent platform.

It exposes four workflows (`change`, `design`, `review`, `test`), three Profiles (`lite`, `standard`, `strict`), five built-in Skills, one Provider execution method, and one deterministic EvidencePolicy/Gate Engine. TaskService owns lifecycle use cases; RunEngine owns durable stage execution and recovery.

Provider output is a claim. The runtime derives trusted facts from workspace diffs, replayed command exit codes and output digests, subject-bound reviews, runtime-confirmed reviewer identity, human interactions, and a hash-chained event log. Hard gates produce `PASS`, `BLOCKED`, or `WAITING_HUMAN`. The four-dimension Scorecard explains evidence quality and can never waive a hard failure.

```powershell
python -m pip install -e ".[test]"
muxdev init
muxdev run "add a deterministic marker" --workflow change --profile lite --provider mock
muxdev evidence verify <run-id>
```

Each run produces one `evidence-report.json`. Explicit export can add an `attestation.dsse.json` that binds the report digest without duplicating its facts. Project Skills remain prompt guidance only; project gate rules belong in `evidence-policy.yaml`.

See the [measured simplification report](../../release-artifacts/simplification-report.md), [architecture diagrams](../../release-artifacts/architecture-diagrams.md), [open-source research record](../../release-artifacts/open-source-research.md), and [Evidence v3 examples](../../release-artifacts/evidence-v3-example.json).
