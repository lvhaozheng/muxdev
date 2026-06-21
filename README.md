# muxdev

`muxdev` is a local-first Agentic SDLC Kernel for the multi-AI-coding-agent era.

It coordinates provider CLIs such as Codex, Claude Code, Qwen, Kimi, Gemini-style tools, OpenCode-style tools, Trae, Cursor-style tools, and the built-in `mock` provider through role-aware workflows, explicit memory, approval-bound delivery, rollback, reports, evidence, and local governance.

Unlike a single AI coding assistant, muxdev does not try to be the smartest coder by itself. It turns multiple AI coding agents into a verifiable, auditable, recoverable, governable, and continuously learning software delivery system.

The goal is simple: make AI coding as easy to start as a CLI command, but as trustworthy as a local software delivery control plane. After an agent changes code, muxdev should tell you what changed, how it was verified, what risk remains, whether it can be merged, and how to roll back.

Current scope: **automation, trusted delivery, runtime safety, ecosystem automation, advanced parallel learning, and product experience are implemented**.

## Why muxdev

- AI coding tools are fragmented across different CLIs, permissions, logs, contexts, and provider behaviors.
- Multi-agent results need evidence, approval integrity, rollback, memory governance, and recovery, not just natural-language confidence.
- muxdev turns provider CLIs into a local trusted software delivery system with daemon-owned state, local artifacts, and visible handoffs.

## Core Capabilities

- **Auto flow selection**: chooses `simple`, `safe`, `deep`, `parallel`, or `ci` based on intent, risk, repo signals, and memory.
- **Role-aware provider routing**: assigns `plan`, `code`, `test`, `review`, `secure`, `docs`, `architect`, and memory roles to suitable providers.
- **Design-first workflows**: `muxdev design` produces a Design Pack before implementation.
- **Evidence-aware memory**: project knowledge can reference evidence ids, but memory is promoted explicitly and stays separate from evidence recording.
- **Evidence v2**: records a lightweight event stream, manifest, and gate-first evaluation instead of legacy heavyweight evidence artifacts.
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
muxdev evidence latest --events
muxdev evidence verify latest --json
muxdev why latest
muxdev report latest
muxdev diff latest
muxdev rollback latest --to-stage code
muxdev undo latest --to-stage code
muxdev ship latest --dry-run
```

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
```

Provider install commands are dry-run by default. Use `--execute` only after reviewing the plan.

## Dashboard And TUI

```powershell
muxdev dashboard
muxdev tui
```

The Dashboard opens as **muxdev Mission Control**, not just a task list. The first screen is organized around:

- **Projects**: the default tab. Tasks are grouped by their execution `workspace`, so the directory where a task runs becomes its project.
- **Workflows / Tasks / Activity / Artifacts / Config**: project detail uses tabs instead of stacking every dashboard surface on one long page. Workflow remains the default view; Activity contains timeline, provider actions, approvals, and events; Artifacts contains evidence, reports, tests, transcripts, rollback, and semantic merge output.
- **Project Hide**: project cards can be hidden from Mission Control. This only archives the dashboard entry; it does not delete the workspace, runs, evidence, or files. Hidden projects can be restored through `POST /api/dashboard/projects/{project_id}/restore`.
- **Global Config**: role templates, provider health, budget, safety gates, Skills Catalog, Workflow Templates, and a compact MCP status strip are collected outside project task flow.
- **Action Center**: the next concrete action translated from daemon/provider state remains visible above the project shell.
- **Task Timeline** and **Evidence / Artifacts Center**: selecting a card opens stage lifecycle, provider attempts, memory context, rollback snapshots, final reports, diffs, tests, transcripts, Evidence v2 evaluation, and semantic merge results.

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
```

## Documentation

- [Product Guide](docs/product_guide.md): positioning, capabilities, complex workflows, and roadmap direction.
- [Architecture](docs/architecture.md): daemon, runtime, storage, workflow, provider, UI, and API architecture.
- [LangGraph And Loop Engineering](docs/langgraph_loop_engineering.md): LangGraph-first runtime direction, loop events, optional RAG policy, and validation signals.
- [Configuration](docs/configuration.md): TOML runtime config, profiles, gates, roles, providers, memory, skills, and troubleshooting.
- [Best Practices](docs/best_practices.md): daily workflows, provider actions, approvals, memory, evidence, dashboard, and testing.
- [Source Walkthrough](docs/source_walkthrough.md): code-level map for contributors.
- [Automation, Design, And Memory](docs/automation_design_memory.md): auto flow selection, role topology, Design Pack, and explicit memory.
- [Trusted Delivery](docs/trusted_delivery.md): Evidence v2, contracts, ledger, approvals, validator, and rollback.
- [Runtime Safety And Provider Stability](docs/runtime_safety_provider.md): provider attempts, provider actions, session capsules, read-only gates, and provider scores.
- [Ecosystem And Automation](docs/ecosystem_automation.md): feedback, CI rescue, cache, skill governance, and lightweight MCP guardrails.
- [Advanced Parallel And Learning](docs/advanced_parallel_learning.md): parallel conflicts, semantic merge, provider learning, memory quarantine, and multi-repo planning.
- [Product Experience](docs/product_experience.md): one-line setup, provider wizard, MUXDEV.md, budget, Git safety, rules, skills, and web UI surface.

## Development

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pytest -q
```

Windows may emit pytest cache warnings if `.pytest_cache` cannot be written. These warnings do not affect the test result.
