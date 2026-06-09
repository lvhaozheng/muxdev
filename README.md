# muxdev

`muxdev` is a local-first Agentic SDLC Kernel for the multi-AI-coding-agent era.

It coordinates provider CLIs such as Codex, Claude Code, Qwen, Kimi, Gemini-style tools, OpenCode-style tools, Trae, Cursor-style tools, and the built-in `mock` provider through role-aware workflows, evidence-grounded memory, approval-bound delivery, rollback, reports, and local governance.

Unlike a single AI coding assistant, muxdev does not try to be the smartest coder by itself. It turns multiple AI coding agents into a verifiable, auditable, recoverable, governable, and continuously learning software delivery system.

The goal is simple: make AI coding as easy to start as a CLI command, but as trustworthy as a local software delivery control plane. After an agent changes code, muxdev should tell you what changed, how it was verified, what risk remains, whether it can be merged, and how to roll back.

Current scope: **P0-P4 implemented**.

## Why muxdev

- AI coding tools are fragmented across different CLIs, permissions, logs, contexts, and provider behaviors.
- Multi-agent results need evidence, approval integrity, rollback, memory governance, and recovery, not just natural-language confidence.
- muxdev turns provider CLIs into a local trusted software delivery system with daemon-owned state, local artifacts, and visible handoffs.

## Core Capabilities

- **Auto flow selection**: chooses `simple`, `safe`, `deep`, `parallel`, or `ci` based on intent, risk, repo signals, and memory.
- **Role-aware provider routing**: assigns `plan`, `code`, `test`, `review`, `secure`, `docs`, `architect`, and memory roles to suitable providers.
- **Design-first workflows**: `muxdev design` produces a Design Pack before implementation.
- **Evidence-grounded memory**: stores project knowledge only with evidence, lifecycle, role scope, and approval state.
- **Evidence Scorecard plus Audit Pack**: shows delivery confidence, risks, missing evidence, coverage, and next actions by default, while preserving stage contracts, role result contracts, evidence bundles, blind validator, semantic merge review, snapshots, and hash ledger for audit.
- **Approval and provider-action handoff**: separates muxdev policy approvals from external provider CLI confirmations, auth, rate limits, and blocked sessions.
- **Rollback and recovery**: isolated worktrees, stage snapshots, reports, traces, session capsules, and resumable runs.
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
muxdev demo --mock
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
- `muxdev demo --mock` runs a complete offline task without external accounts.
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
muxdev evidence latest --audit
muxdev evidence verify latest --json
muxdev why latest
muxdev report latest
muxdev diff latest
muxdev rollback latest --to-stage code
muxdev undo latest --to-stage code
muxdev ship latest --dry-run
```

## Evidence Scorecard

muxdev keeps the full Audit Pack, but the default delivery view is a human-readable Scorecard:

```text
Delivery Confidence: 84 / 100  reviewable
Recommendation: merge_after_review

why:
- targeted tests passed
- blind validator accepted the run
- rollback snapshot available

missing evidence:
- full regression not run
- negative-path test missing
```

Each completed run writes:

- `evidence/scorecard.json`: weighted Delivery Confidence Score and recommendation.
- `evidence/coverage_matrix.json`: acceptance criteria mapped to implementation, tests, and review.
- `evidence/human_summary.md`: readable summary for handoff and review.
- Audit Pack artifacts: contracts, evidence bundles, validator panel, semantic merge review, ledger, snapshots, trace, and session capsules.

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
```

Provider install commands are dry-run by default. Use `--execute` only after reviewing the plan.

## Dashboard And TUI

```powershell
muxdev dashboard
muxdev tui
```

The Dashboard opens as **muxdev Mission Control**, not just a task list. The first screen is organized around:

- **Current Status**: running tasks, stuck tasks, provider actions, muxdev approvals, and recent completed deliverables.
- **Action Center**: the next concrete action translated from daemon/provider state.
- **Task Board**: Todo / Running / Waiting / Needs Review / Done / Failed with provider, workflow, status, branch, risk, and cost filters.
- **Task Timeline**: stage lifecycle plus current stage, provider attempts, memory context, rollback snapshots, and advanced state.
- **Evidence / Artifacts Center**: final reports, diffs, test output, provider transcripts, stage contracts, evidence bundles, snapshots, rollback points, and semantic merge results.

Provider actions are rendered as a card-style wizard: copy the attach command, handle the external provider CLI prompt yourself, then click `Mark handled and continue`. muxdev approvals are rendered as risk-review cards with approve/deny, diff, and evidence actions.

The TUI accepts natural-language tasks by default: type `fix the failing login test` and muxdev submits the default dev flow. Slash commands remain available for expert actions. It does not type `yes/no` into provider CLIs; for Provider Actions, handle the provider CLI/session first, then mark the action handled and continue.

UX-focused API endpoints:

```text
GET  /api/ux/overview
GET  /api/tasks/{run_id}/ux
POST /api/tasks/{run_id}/actions/{action_id}/handled-and-continue
GET  /api/setup/status
GET  /api/providers/health
```

## Documentation

- [Product Guide](docs/product_guide.md): positioning, capabilities, complex workflows, and roadmap direction.
- [Architecture](docs/architecture.md): daemon, runtime, storage, workflow, provider, UI, and API architecture.
- [Configuration](docs/configuration.md): TOML runtime config, profiles, gates, roles, providers, memory, skills, and troubleshooting.
- [Best Practices](docs/best_practices.md): daily workflows, provider actions, approvals, memory, evidence, dashboard, and testing.
- [Source Walkthrough](docs/source_walkthrough.md): code-level map for contributors.
- [P0 Acceptance](docs/p0_acceptance_ready.md): auto + role + design + memory.
- [P1 Acceptance](docs/p1_trusted_delivery_ready.md): trusted delivery loop.
- [P2 Acceptance](docs/p2_runtime_safety_provider_ready.md): runtime safety and provider handoff.
- [P3 Acceptance](docs/p3_ecosystem_automation_ready.md): feedback, cache, skill, plugin, and guardrail loop.
- [P4 Product Experience Acceptance](docs/p4_product_experience_ready.md): one-line setup, provider wizard, MUXDEV.md, budget, Git safety, rules, skills, and web UI surface.
- [P4 Acceptance](docs/p4_advanced_parallel_learning_ready.md): parallel, semantic merge, learning, memory quarantine, and multi-repo planning.

## Development

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pytest -q
```

Current full suite:

```text
129 passed
```

Windows may emit pytest cache warnings if `.pytest_cache` cannot be written. These warnings do not affect the test result.
