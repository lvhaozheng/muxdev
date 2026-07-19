# muxdev

`muxdev` is a local-first trusted delivery control plane for coding-agent
harnesses.

It runs provider CLIs such as Codex and Qwen inside task-scoped workspaces and
keeps workflow state, approvals, rollback material, reports, and evidence under
local control. The built-in `mock` provider supplies deterministic offline
validation.

Unlike a coding model, muxdev is responsible for the delivery boundary around
the model: which task ran, which provider was used, what changed, which checks
ran, where human approval was required, and how the change can be recovered.

The implemented v0.2 increments add reliable storage, durable task recovery,
certified harness capabilities, risk-tiered isolation, capability-constrained
routing, heterogeneous read-only review, signed offline delivery attestations,
a task-first Dashboard, deterministic benchmark Replay and authenticated local
API access.

Repository status: the Week 1 baseline through Week 8 release-candidate
increment is implemented as `0.2.0rc1`. It is not a final public release and no
real Provider quality improvement is claimed until the explicit live Benchmark
gate passes.

Status labels: **当前已实现 (Current v0.2 RC)** means code on the main path today;
**v0.2 目标 (Target)** means planned behavior that must not be presented as shipped.

Current v0.2 RC scope: **local trusted task execution, durable recovery,
capability routing, independent review, signed delivery, Replay benchmark and
task-first product surfaces**.
Experimental surfaces are identified in the [v0.2 plan](docs/v0.2_trusted_agent_harness_plan.md).

## Why muxdev

- AI coding tools are fragmented across different CLIs, permissions, logs, contexts, and provider behaviors.
- Multi-agent results need evidence, approval integrity, rollback, memory governance, and recovery, not just natural-language confidence.
- muxdev turns provider CLIs into a local trusted software delivery system with daemon-owned state, local artifacts, and visible handoffs.

## Core Capabilities

- **Deterministic flow selection**: current intent, risk, and repository signals choose a workflow; this is policy logic, not learned optimal routing.
- **Capability-constrained routing (v0.2 Week 5 implemented)**: a task-level immutable decision filters certified capabilities, isolation, production/simulation mode, allowlists, and budget before applying a conservative evidence-backed quality score.
- **Heterogeneous read-only review (v0.2 Week 5 implemented)**: high-risk tasks require a different, fingerprint-distinct Provider with verified read-only capability, or a subject-bound human waiver before launch.
- **Signed delivery and offline trust (v0.2 Week 6 implemented)**: per-project Ed25519 identities atomically bind completed state, route, isolation, review, approvals, tests, Evidence, and allowlisted artifacts into a safe `.muxattest` bundle.
- **Task-first product (v0.2 Week 7 implemented)**: one no-build Dashboard explains current action, route, recovery, review, isolation, Evidence and signed delivery through `TaskStory v1`.
- **Trusted benchmark and local auth (v0.2 Week 8 implemented)**: CI runs a deterministic 24-case Replay matrix, real Provider execution remains explicit and budgeted, and the local API uses bearer/cookie bootstrap authentication.
- **Design-first workflows**: `muxdev design` produces a Design Pack before implementation.
- **Evidence-aware memory**: project knowledge can reference evidence ids, but memory is promoted explicitly and stays separate from evidence recording.
- **Evidence v2**: records a lightweight event stream, manifest, and gate-first evaluation instead of legacy heavyweight evidence artifacts.
- **Approval and provider-action handoff**: separates muxdev policy approvals from external provider CLI confirmations, auth, rate limits, and blocked sessions.
- **Durable local runtime (v0.2 Week 3 implemented)**: a bounded Worker Pool, persistent Jobs, fenced leases, heartbeats, cooperative cancellation, safe retry, and restart reconciliation.
- **Reliable local state (v0.2 Week 2 implemented)**: WAL/FULL SQLite, checksummed migrations, event-first core lifecycle state, replay comparison, and verified backup/restore.
- **Certified Agent Harness (v0.2 Week 4 implemented)**: versioned Codex/Qwen/Mock/Replay lifecycles, evidence-backed capability certification, hash-chained Attempt events, and approval-gated high-risk isolation downgrade.
- **Dashboard/TUI/API**: local daemon, Web Dashboard, terminal UI, JSON output, and automation-friendly APIs.

## 3-Minute Quick Start

Start here even if you have no AI provider CLI installed yet. The built-in
`mock` provider runs a deterministic offline workflow, so you can see the full
task lifecycle, report, diff, evidence, and TUI before connecting Codex, Claude
Code, Qwen, or another provider.

```powershell
pipx install muxdev
# or: uv tool install muxdev

muxdev setup --project
muxdev provider setup
muxdev demo --scenario trusted-delivery-v1 --mode replay
muxdev
```

When developing from this repository, use:

```powershell
cd D:\jianzhi\lyuShao\muxdev
python -m pip install -e ".[test]"
muxdev setup --project
```

After setup:

```powershell
muxdev "fix the failing login test"
muxdev status latest
muxdev evidence latest
muxdev dashboard
muxdev experience
```

What this does:

- `muxdev setup --project` writes safe defaults and creates `MUXDEV.md` as the project context anchor.
- `muxdev provider setup` shows install, login, and doctor steps for every provider.
- `muxdev doctor` checks daemon health, provider CLIs, Git, API/Dashboard ports, memory DB, worktree writes, and the mock provider.
- `muxdev demo --scenario trusted-delivery-v1 --mode replay` runs the four-minute, side-effect-free trusted-delivery story without external accounts.
- `muxdev` opens the guided daemon TUI. You can type a task in plain English or use slash commands such as `/doctor`, `/dev`, `/actions`, `/approvals`, and `/report`.
- `muxdev experience` summarizes install, provider health, budget, Git safety, rules, skills, and web/IDE extension surfaces.

Default local URLs:

- Dashboard: `http://127.0.0.1:8787`
- API: `http://127.0.0.1:8788`

If the CLI or TUI returns a daemon 404 after code changes, restart the local daemon:

```powershell
muxdev serve --restart
```

### Provider Actions Vs muxdev Approvals

These are intentionally different safety gates:

- **Provider Action** means an external provider CLI is waiting for you, such as a permission prompt, login, rate-limit recovery, or blocked terminal session. muxdev shows the reason and attach command, but it does not type `yes/no` into provider CLIs. Handle the provider prompt yourself, then use `muxdev action handled <id>` or the Dashboard's handled-and-continue action.
- **muxdev Approval** means muxdev itself is asking you to review risk before it proceeds, such as writing files, running shell commands, merging, using network access, installing dependencies, or touching sensitive areas. Review the evidence and diff, then approve or deny from the CLI, TUI, or Dashboard.

## Common Commands

```powershell
muxdev "fix the failing login test"
muxdev init --wizard
muxdev doctor
muxdev demo --mock
muxdev design "design persistent project memory"
muxdev design --simple "design a small snake game"
muxdev dev "add Redis rate limiting"
muxdev dev --from-design latest
muxdev fix "fix login tests"
muxdev refactor "split billing module" --parallel
muxdev review
muxdev test
muxdev ci fix
muxdev evidence latest
muxdev evidence latest --events
muxdev evidence verify latest --json
muxdev storage status
muxdev storage backup --scope all
muxdev storage replay latest
muxdev runtime status
muxdev task executions latest
muxdev task cancel latest --reason "operator requested stop" --wait
muxdev task reconcile latest --retry --reason "transcript reviewed" --yes
muxdev provider certify codex --offline
muxdev provider certification codex
muxdev task harness-events latest
muxdev task isolation latest
muxdev task route latest
muxdev task review latest
muxdev routing replay latest
muxdev routing benchmark trusted-routing-v1
muxdev benchmark run trusted-routing-v1 --mode replay
muxdev benchmark status
muxdev trust status
muxdev attestation show latest
muxdev attestation export latest --output delivery.muxattest
muxdev attestation verify delivery.muxattest
muxdev auth status
muxdev doctor --production
muxdev why latest
muxdev report latest
muxdev diff latest
muxdev rollback latest --to-stage code
muxdev undo latest --to-stage code
muxdev ship latest --dry-run
```

Week 5 routing and review details are documented in the
[capability routing architecture](docs/v0.2_week5_capability_routing_review.md)
and [ADR 0005](docs/adr/0005-quality-first-routing-and-heterogeneous-review.md).
Adapter certification remains documented in [Week 4](docs/v0.2_week4_certified_harness.md).
Signed completion, key governance, and offline verification are documented in
[Week 6](docs/v0.2_week6_signed_delivery_attestation.md),
[ADR 0006](docs/adr/0006-project-signing-and-offline-trust.md), and the
[`.muxattest` format](docs/muxattest_format.md).

## Evidence v2

muxdev records evidence as a lightweight event stream plus derived manifest and evaluation:

```text
label: reviewable
confidence: 0.83
events: 24
head_hash: sha256:...

reasons:
- targeted tests passed
- blind validator accepted the run
- rollback snapshot available

missing evidence:
- none
```

Each completed run writes:

- `evidence/events.jsonl`: append-only Evidence v2 events with artifact refs and hash chaining.
- `evidence/manifest.json`: counts, required evidence matrix, missing required evidence, and head hash.
- `evidence/evaluation.json`: gate-first label, confidence, reasons, missing evidence, and next actions.
- Existing runtime artifacts such as role contracts, validator panel, semantic merge review, ledger, snapshots, trace, and session capsules remain available, but legacy heavyweight evidence artifacts are no longer generated.

Useful waiting-state commands:

```powershell
muxdev approvals --status pending --json
muxdev actions --status pending --json
muxdev continue latest
```

## Memory Governance

muxdev separates temporary context from long-term memory. Session, run, and branch memory stay scoped until reviewed; project, workspace, and user memory require explicit promotion before they become durable provider context.

```powershell
muxdev memory status
muxdev memory inbox
muxdev memory query "pytest" --layers project,workspace,user
muxdev memory promote mem_123 --layer project
```

Before each provider stage, muxdev writes a context packet to `context_packets/<stage>.json`, records the packet hash in the ledger, and excludes quarantined or contradictory memory from the provider task.

## Providers

muxdev uses provider CLIs as execution backends while keeping workflow state, approvals, evidence, and recovery in muxdev. The built-in `mock` provider is deterministic and useful for smoke tests.

```powershell
muxdev provider detect
muxdev provider doctor codex --json
muxdev provider account codex
muxdev provider install codex
muxdev provider certify codex --offline
muxdev provider certification codex
```

Provider install commands are dry-run by default. Use `--execute` only after reviewing the plan.

## Dashboard And TUI

```powershell
muxdev dashboard
muxdev tui
```

The current Dashboard is a local operational view over projects, tasks, provider
readiness, and manual actions. The v0.2 plan replaces the configuration-heavy
overview with a task-first route and delivery view. Current surfaces include:

- **Projects**: the default tab. Tasks are grouped by their execution `workspace`, so the directory where a task runs becomes its project.
- **Workflows / Tasks / Activity / Artifacts / Config**: project detail uses tabs instead of stacking every dashboard surface on one long page. Workflow remains the default view; Activity contains timeline, provider actions, approvals, and events; Artifacts contains evidence, reports, tests, transcripts, rollback, and semantic merge output.
- **Project Hide**: project cards can be hidden from Mission Control. This only archives the dashboard entry; it does not delete the workspace, runs, evidence, or files. Hidden projects can be restored through `POST /api/dashboard/projects/{project_id}/restore`.
- **Global Config**: role templates, provider health, budget, safety gates, Skills Catalog, Workflow Templates, and a compact MCP status strip are collected outside project task flow.
- **Action Center**: the next concrete action translated from daemon/provider state remains visible above the project shell.
- **Task Timeline** and **Evidence / Artifacts Center**: selecting a task exposes stage lifecycle, provider attempts, rollback snapshots, reports, diffs, tests, transcripts, and Evidence v2 evaluation. Semantic-merge output remains experimental.

Provider actions are rendered as a card-style wizard: copy the attach command, handle the external provider CLI prompt yourself, then click `Mark handled and continue`. muxdev approvals are rendered as risk-review cards with approve/deny, diff, and evidence actions.

The TUI accepts natural-language tasks by default: type `fix the failing login test` and muxdev submits the default dev flow. Slash commands remain available for expert actions. It does not type `yes/no` into provider CLIs; for Provider Actions, handle the provider CLI/session first, then mark the action handled and continue.

UX-focused API endpoints:

```text
GET  /api/ux/overview
GET  /api/dashboard/overview
GET  /api/tasks/{run_id}/ux
POST /api/tasks/{run_id}/actions/{action_id}/handled-and-continue
GET  /api/setup/status
GET  /api/providers/health
GET  /api/providers/certifications
GET  /api/providers/{name}/certification
GET  /api/tasks/{run_id}/harness-events
GET  /api/tasks/{run_id}/isolation
GET  /api/tasks/{run_id}/route
GET  /api/tasks/{run_id}/review
GET  /api/routing/snapshots
POST /api/routing/replays
POST /api/routing/benchmarks
GET  /api/trust/status
GET  /api/tasks/{run_id}/attestation
POST /api/tasks/{run_id}/attestation-exports
POST /api/attestation-exports/{export_id}/verify
```

## Documentation

- [v0.2 Trusted Agent Harness Plan](docs/v0.2_trusted_agent_harness_plan.md): accepted product contract, target architecture, eight-week sequence, benchmark, and stable/experimental boundary.
- [v0.2 Threat Model](docs/v0.2_threat_model.md): workspace, API, agent, credential, sandbox, evidence, and scheduler threats.
- [ADR 0001](docs/adr/0001-v0.2-product-contract.md): task-unit team model, routing priority, first adapters, and production boundary.
- [v0.2 Week 2 State And Storage](docs/v0.2_week2_state_storage.md): implemented reducer, events, SQLite durability, migrations, replay, backup/restore, and demo path.
- [ADR 0002](docs/adr/0002-event-driven-state-and-reliable-sqlite.md): decision record for lifecycle event sourcing and reliable local SQLite.
- [v0.2 Week 3 Durable Runtime](docs/v0.2_week3_durable_runtime.md): persistent jobs, leases, fencing, cancellation, and reconciliation.
- [ADR 0003](docs/adr/0003-durable-runtime-and-safe-recovery.md): decision record for bounded at-least-once execution and safe recovery.
- [v0.2 Week 4 Certified Harness](docs/v0.2_week4_certified_harness.md): certified Adapter lifecycle, Harness Events, risk policy, isolation, and demo path.
- [ADR 0004](docs/adr/0004-certified-adapters-and-tiered-isolation.md): decision record for evidence-backed capabilities and approval-gated isolation downgrade.
- [v0.2 Week 5 Capability Routing And Review](docs/v0.2_week5_capability_routing_review.md): immutable quality-first routing, route replay, heterogeneous review, waivers, and management interfaces.
- [ADR 0005](docs/adr/0005-quality-first-routing-and-heterogeneous-review.md): decision record for capability-gated routing and independent read-only review.
- [v0.2 Week 6 Signed Delivery Attestation](docs/v0.2_week6_signed_delivery_attestation.md): per-project identities, atomic signed completion, offline trust, security boundary, and demo path.
- [ADR 0006](docs/adr/0006-project-signing-and-offline-trust.md): decision record for Ed25519 project identity, fail-closed risk policy, and self-contained verification.
- [v0.2 Week 7 Task-First Product](docs/v0.2_week7_task_first_product.md): TaskStory, the no-build Dashboard, privacy bounds, and Replay/Live managed demo.
- [ADR 0007](docs/adr/0007-task-story-and-no-build-dashboard.md): decision record for one task-first read model and one Dashboard.
- [Four-Minute Demo](docs/demo/v0.2_four_minute_demo.md): deterministic interview script and the simulation boundary.
- [v0.2 Week 8 Benchmark And RC](docs/v0.2_week8_benchmark_release.md): Benchmark v2/v7, local authentication, release gates, and packaging.
- [ADR 0008](docs/adr/0008-dual-track-benchmark-and-local-rc.md): decision record for Replay/live separation and local-only RC artifacts.
- [`.muxattest` Format](docs/muxattest_format.md): deterministic bundle members, exclusions, parser limits, and offline verification.
- [trusted-routing-bench](docs/benchmarks/trusted-routing-bench.md): executable 24-case v2 contract and the explicit boundary between Replay and measured live results.
- [Product Guide](docs/product_guide.md): positioning, capabilities, complex workflows, and roadmap direction.
- [Architecture](docs/architecture.md): daemon, runtime, storage, workflow, provider, UI, and API architecture.
- [LangGraph And Loop Engineering](docs/langgraph_loop_engineering.md): historical/experimental graph direction; LangGraph is not a stable v0.2 main-path claim.
- [Configuration](docs/configuration.md): TOML runtime config, profiles, gates, roles, providers, memory, skills, and troubleshooting.
- [Best Practices](docs/best_practices.md): daily workflows, provider actions, approvals, memory, evidence, dashboard, and testing.
- [Source Walkthrough](docs/source_walkthrough.md): code-level map for contributors.
- [Automation, Design, And Memory](docs/automation_design_memory.md): auto flow selection, role topology, Design Pack, and explicit memory.
- [Trusted Delivery](docs/trusted_delivery.md): Evidence v2, contracts, ledger, approvals, validator, and rollback.
- [Runtime Safety And Provider Stability](docs/runtime_safety_provider.md): provider attempts, provider actions, session capsules, read-only gates, and provider scores.
- [Ecosystem And Automation](docs/ecosystem_automation.md): feedback, CI rescue, cache, skill governance, and lightweight MCP guardrails.
- [Advanced Parallel And Learning](docs/advanced_parallel_learning.md): experimental parallel, semantic merge, score aggregation, memory quarantine, and multi-repo planning.
- [Product Experience](docs/product_experience.md): one-line setup, provider wizard, MUXDEV.md, budget, Git safety, rules, skills, and web UI surface.

## Development

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python scripts/verify_cross_platform.py --suite quick
python scripts/verify_cross_platform.py --suite full
```

快速套件排除标记为 `integration` 和 `release` 的用例。核心完整套件将测试文件分配给两个相互隔离的 pytest 进程，执行全部非 `release` 用例并强制六分钟门槛；浏览器、Benchmark Replay、性能、安全归档与打包验证由独立 release job 执行。pytest 缓存与测试日志写入仓库内已忽略的 `.test_workspaces/`。
