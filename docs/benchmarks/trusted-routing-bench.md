# trusted-routing-bench

## Current Status

**当前已实现:** `trusted-routing-v1` is an executable v2, path-restricted
contract with 24 cases across six task classes. Replay produces the complete
48-result Codex/Qwen simulation matrix, four counterfactual policies,
task-class-stratified 10,000-sample confidence intervals, and a deterministic
safe result package without shell, network, Provider or production-learning
effects. Live execution is CLI-only and requires explicit confirmation plus a
positive budget.

**v0.2 目标:** run the live gate against certified real Codex and Qwen fixtures,
publish the resulting signed report, and permit versioned routing priors only
after all authenticity and coverage gates pass. No real Provider-quality claim
has been measured in this repository increment.

## Case Matrix

The manifest covers four cases in each class:

- bugfix;
- feature;
- refactor;
- test;
- documentation;
- security/migration.

Every case contains a stable case id, class, repository Fixture identifier,
hidden-test identifier, risk tags, and required capabilities. Callers cannot
supply an arbitrary local Fixture path through CLI or HTTP.

## Strategies And Metrics

Plans compare fixed Codex, fixed Qwen, legacy rules, and the v0.2 router. The
declared metrics are verified success, quality-adjusted regret, unsafe routing,
capability mismatch, reviewer coverage, p90 cost/latency, human intervention,
and evidence completeness.

The repository does not contain pre-written wins or synthetic quality claims.
Replay percentages exercise reporting only and remain marked simulation.

## Commands

```powershell
muxdev benchmark plan trusted-routing-v1 --json
muxdev benchmark run trusted-routing-v1 --mode replay --json
muxdev benchmark status --json

# Cost-bearing execution is CLI-only and never runs in default CI/HTTP.
muxdev benchmark run trusted-routing-v1 --mode live --yes --max-cost-usd 10 --json
```

The release gate remains zero high-risk capability mismatch and no more than
15% quality-adjusted regret versus the per-task oracle. It is a target until a
real held-out run is published.
