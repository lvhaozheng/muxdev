# muxdev

`muxdev` is a local-first Agentic SDLC control plane for coordinating provider CLIs, role-aware workflows, approvals, evidence, memory, dashboards, TUI supervision, provider handoffs, feedback routing, and long-term learning from one command surface.

It is built around a localhost daemon and a SQLite blackboard. Provider CLIs such as Codex, Claude Code, Qwen, Kimi, Gemini-style CLIs, or the built-in `mock` provider run inside staged workflows, while muxdev owns the state, safety gates, evidence trail, memory lifecycle, and human handoff model.

Current scope: **P0-P4 implemented**.

## Contents

- [Quick Start](#quick-start)
- [What muxdev does](#what-muxdev-does)
- [Installation](#installation)
- [Provider Setup](#provider-setup)
- [Core Commands](#core-commands)
- [Approvals And Provider Actions](#approvals-and-provider-actions)
- [Evidence, Safety, And Rollback](#evidence-safety-and-rollback)
- [Memory And Learning](#memory-and-learning)
- [Feedback, Cache, Skills, And Plugins](#feedback-cache-skills-and-plugins)
- [Dashboard And TUI](#dashboard-and-tui)
- [MCP And Integrations](#mcp-and-integrations)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Development](#development)
- [Documentation](#documentation)

## Quick Start

```powershell
cd D:\jianzhi\lyuShao\muxdev
python -m pip install -e ".[test]"

muxdev setup --project --yes
muxdev start
muxdev provider detect
muxdev dev "add rate limiting and tests" --provider mock --json
muxdev status latest
muxdev dashboard
muxdev tui
```

Default local URLs:

- Dashboard: `http://127.0.0.1:8787`
- API: `http://127.0.0.1:8788`

If the TUI or CLI returns a 404 after code changes, the running daemon is old:

```powershell
muxdev serve --restart
```

## What muxdev does

- **Simple entrypoints**: `design`, `dev`, `fix`, `refactor`, `review`, `test`, `ci fix`, `why`, `report`, `undo`-style rollback commands.
- **Auto orchestration**: intent resolver, auto flow selector, and role topology compiler choose `simple`, `safe`, `deep`, `parallel`, or `ci` and compile roles automatically.
- **Role-aware execution**: built-in role routing for `plan`, `code`, `test`, `review`, `secure`, `docs`, `architect`, `requirements`, `test_strategy`, and `memory_curator`.
- **Design as a first-class workflow**: `muxdev design` writes a Design Pack and memory proposals without implementing code.
- **Trusted delivery**: stage contracts, role result contracts, evidence bundles, blind validator panels, hash-chained ledgers, approval subject hashes, snapshots, and rollback.
- **Provider action handoff**: provider CLI confirmations, auth prompts, rate limits, idle timeouts, and blocked sessions are tracked as Provider Actions, not muxdev approvals.
- **Runtime safety**: read-only stage write detection, streaming prompt/action parsing, session capsules, provider attempts, provider scores, and adaptive routing.
- **Ecosystem automation**: feedback router, CI rescue, CAS cache, skill lock, safe plugin manifest, MCP guardrail tools, and dashboard visibility.
- **Advanced parallel and learning**: conflict-aware parallel-squad, semantic merge reviewer, provider learning snapshots, memory contradiction detection/quarantine, and multi-repo orchestration planning.

## Installation

Requirements:

- Python 3.11+
- Git
- Optional provider CLIs such as Codex CLI, Claude Code, Qwen Code, Kimi Code, or other configured headless CLIs
- Optional `tmux` on POSIX for native attach workflows

Install from this repository:

```bash
python -m pip install -e ".[test]"
```

Verify:

```bash
muxdev --version
python -m muxdev --version
python -m pytest -q
```

## Provider Setup

```powershell
muxdev provider detect
muxdev provider detect --json
muxdev provider doctor codex --json
muxdev provider account codex
muxdev provider install codex
```

Install commands are dry-run by default. Use `--execute` only after reviewing the plan.

Built-in provider names include:

| Provider | Use | Install/login note |
| --- | --- | --- |
| `mock` | deterministic local tests | built in |
| `codex` | OpenAI Codex CLI | `npm install -g @openai/codex`, then `codex login` |
| `claude-code` | Claude Code | `npm install -g @anthropic-ai/claude-code`, then first `claude` run |
| `qwen` | Qwen Code | `npm install -g @qwen-code/qwen-code@latest`, then `qwen auth` |
| `kimi` | Kimi Code | `npm install -g @moonshot-ai/kimi-code@latest`, then `kimi login` |
| `trae`, `antigravity` | manual or experimental adapters | product-specific setup |

## Core Commands

### Design, Dev, Fix, Review, Test

```powershell
muxdev design "design evidence-grounded memory" --provider mock --json
muxdev dev "implement persistent memory" --provider mock --json
muxdev dev --from-design latest --provider mock --json
muxdev fix "fix login test" --provider mock --json
muxdev refactor "split billing module" --parallel --provider mock --json
muxdev review --provider mock --json
muxdev test --provider mock --json
muxdev ci fix --provider mock --json
```

Override automation when needed:

```powershell
muxdev dev "small copy fix" --simple
muxdev dev "security-sensitive auth change" --deep
muxdev dev "parallel module migration" --parallel
muxdev dev "ship feature" -p pair --role code=codex --role review=claude-code
```

Explain routing:

```powershell
muxdev why latest
muxdev why latest --json
```

### Task Lifecycle

```powershell
muxdev tasks
muxdev status latest
muxdev continue latest
muxdev stop <run_id>
muxdev retry latest --stage review
muxdev skip latest --stage review --reason "accepted risk"
muxdev report latest
muxdev diff latest
```

Each daemon run writes state under `~/.muxdev/data/`:

```text
~/.muxdev/data/
  muxdev.sqlite
  runs/<run_id>/
    task.md
    task_context.json
    workflow.yaml
    trace.jsonl
    diff.patch
    final_report.md
    contracts/
    evidence/
    snapshots/
    validation/
    session/
    capsules/
```

## Approvals And Provider Actions

muxdev separates two different kinds of human involvement:

- **Approvals** are muxdev policy gates: plan, write, shell, merge, and related integrity checks.
- **Provider Actions** are external provider CLI/session handoffs: CLI confirmations, auth prompts, rate limits, idle timeouts, and provider-blocked states.

Approvals:

```powershell
muxdev dev "needs plan review" --provider mock --require-approval plan --json
muxdev approvals --status pending --json
muxdev approve <approval_id>
muxdev deny <approval_id>
muxdev continue latest
```

Provider Actions:

```powershell
muxdev actions --status pending --json
muxdev attach <run_id> --agent code
muxdev action handled <action_id>
muxdev action dismiss <action_id>
muxdev continue <run_id>
```

When a pending provider action exists, `continue` returns `awaiting_provider_action` and does not start another worker. This prevents repeated provider CLI loops.

## Evidence, Safety, And Rollback

```powershell
muxdev evidence verify latest --json
muxdev rollback latest --to-stage implement --json
muxdev policy shell "pytest" --json
muxdev policy shell "rm -rf /" --json
```

Trusted delivery behavior:

- Every stage can write a `StageContract`.
- Provider outputs can be normalized into `RoleResultContract`.
- Evidence bundles include artifact descriptors and patch hashes.
- The final delivery path writes a Blind Validator Panel and a Semantic Merge Review.
- Merge approval subjects include stable hashes for patch, validator, semantic review, and policy state.
- Stage snapshots support rollback inside the isolated run worktree.
- Read-only stages are blocked if they mutate the worktree.

## Memory And Learning

Project memory is evidence-grounded and stored in `.muxdev/memory.sqlite`.

```powershell
muxdev memory status
muxdev memory query "auth boundary" --json
muxdev memory propose latest --json
muxdev memory approve <mem_id>
muxdev memory quarantine <mem_id>
muxdev memory contradictions --json
muxdev memory quarantine-auto --json
```

Provider learning:

```powershell
muxdev provider score --json
muxdev learning provider --json
muxdev learning provider --role code --json
```

Parallel and semantic merge visibility:

```powershell
muxdev parallel conflicts --file writes.json --json
muxdev parallel conflicts --status open --json
```

Multi-repo planning:

```powershell
muxdev multirepo plan "coordinate auth API change" --repo path\to\repo-a --repo path\to\repo-b --mode design --json
muxdev multirepo dev "coordinate auth API change" --repo path\to\repo-a --repo path\to\repo-b --json
```

P4 v1 generates auditable plans and commands; it does not automatically edit multiple repositories.

## Feedback, Cache, Skills, And Plugins

Feedback and CI rescue:

```powershell
muxdev feedback add ci_failed "pytest failed in tests/test_login.py" --source github-actions --provider mock --json
muxdev feedback list --json
muxdev ci rescue "npm test failed on auth flow" --source github-actions --provider mock --json
```

CAS cache:

```powershell
muxdev cache list --json
```

Skills:

```powershell
muxdev skill add .\.agents\skills\api-review
muxdev skill list --json
muxdev skill show api-review
muxdev skill bind review api-review --project
muxdev skill lock --json
muxdev skill doctor
```

Safe plugin manifest:

```powershell
muxdev plugin validate path\to\plugin --json
muxdev plugin add path\to\plugin --json
muxdev plugin list
```

Plugin commands are a safe local registry and manifest validator in this phase. They do not download code or execute plugin hooks.

## Dashboard And TUI

```powershell
muxdev dashboard
muxdev tui
muxdev tui latest
```

The Dashboard shows tasks, timeline, approvals, provider actions, provider attempts, session capsules, feedback, CI rescue, CAS cache, skill lock, plugin manifests, memory context, guardrails, parallel conflicts, semantic merge reviews, provider learning, multi-repo orchestration, tests, blockers, artifacts, usage, and trace events.

TUI slash commands include:

```text
/dev <task>
/design <task>
/fix <task>
/review [task]
/test [task]
/tasks
/status [id]
/continue [id]
/approvals
/actions
/parallel
/learning
/report [id]
/diff [id]
/dashboard
/quit
```

The TUI does not type `yes/no` into provider CLIs. For Provider Actions it shows the prompt, options, and attach command; handle the provider CLI/session first, then run `/action handled <id>` and `/continue <id>`.

## MCP And Integrations

```powershell
muxdev mcp manifest --json
muxdev mcp serve --request '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
muxdev graph export --json
muxdev deep-agent plan "ship the feature" --json
muxdev session start mock --command "python -m http.server 9999" --json
muxdev session list --json
```

MCP tools include:

- `provider.detect`
- `workspace.search`
- `rag.query`
- `workflow.plugins`
- `workflow.render`
- `flow.list`
- `muxdev.check_policy`
- `muxdev.ask_approval`
- `muxdev.write_event`
- `muxdev.register_artifact`
- `muxdev.read_blackboard`
- `muxdev.query_memory`
- `muxdev.verify_patch`
- `muxdev.get_acceptance_criteria`

## Configuration

Inspect configuration:

```powershell
muxdev config
muxdev config source
muxdev config check
muxdev config paths
muxdev config show --json
```

TOML runtime load order:

1. Built-in runtime defaults
2. Global config: `~/.muxdev/config.toml`
3. Project config: `<repo>/.muxdev/config.toml`
4. Task file from `muxdev dev -f task.toml` or `MUXDEV_TASK_CONFIG`
5. CLI options

Minimal project config:

```toml
version = 2
profile = "auto"
gate = "safe"

[automation]
mode = "auto"
profile = "auto"
depth = "auto"
allow_parallel = true

[roles]
plan = "auto"
code = "auto"
test = "auto"
review = "auto"
secure = "auto"
docs = "auto"
architect = "auto"
memory_curator = "auto"

[memory]
enabled = true
mode = "evidence-grounded"
local_only = true
ttl_days = 180
max_items_per_role = 8
redact_secrets = true
```

See [docs/configuration.md](docs/configuration.md) for details.

## Project Structure

```text
muxdev/
  docs/
  examples/
  plugins/
  scripts/
  src/muxdev/
    api/           FastAPI, live dashboard, MCP JSON-RPC
    cli/           Typer commands and TUI client loop
    clients/       daemon HTTP client, stream/session backends
    config/        TOML runtime config and default YAML compatibility data
    core/          platform, redaction, safety helpers
    daemon/        process manager, server, task manager
    models/        statuses and domain models
    providers/     provider registry and runtime adapters
    runtime/       supervisor runtime and worktree handling
    services/      automation, design, evidence, feedback, cache, learning, dashboard, skills
    storage/       SQLite blackboard, ledger, memory, trace
    ui/            Rich TUI/REPL rendering
    workflows/     workflow parser and DAG helpers
  tests/
```

## Development

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pytest -q
```

Current full suite after P4:

```text
116 passed
```

Windows may emit pytest cache warnings if `.pytest_cache` cannot be written. These warnings do not affect the test result.

## Documentation

- [Architecture](docs/architecture.md)
- [Best practices](docs/best_practices.md)
- [Configuration](docs/configuration.md)
- [Source walkthrough](docs/source_walkthrough.md)
- [P0 acceptance ready guide](docs/p0_acceptance_ready.md)
- [P1 trusted delivery ready guide](docs/p1_trusted_delivery_ready.md)
- [P2 runtime safety/provider ready guide](docs/p2_runtime_safety_provider_ready.md)
- [P3 ecosystem automation ready guide](docs/p3_ecosystem_automation_ready.md)
- [P4 advanced parallel/learning ready guide](docs/p4_advanced_parallel_learning_ready.md)
