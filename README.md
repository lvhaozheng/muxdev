# muxdev

`muxdev` is a local AI coding client-server control plane for coordinating provider CLIs, staged workflows, approvals, traces, reports, skills, and lightweight TUI/REPL workflows from one command.

It is built for local-first development: a localhost daemon, a Web Dashboard, safe provider discovery, dry-run installs, deterministic mock runs, JSON automation output, Windows-friendly command handling, and dynamic project configuration.

Current scope: **M0-M7**.

## Contents

- [Quick Start](#quick-start)
- [Features](#features)
- [Installation](#installation)
- [Provider Setup](#provider-setup)
- [Usage](#usage)
- [Configuration](#configuration)
- [Workflow Plugins And Flows](#workflow-plugins-and-flows)
- [MCP And Integrations](#mcp-and-integrations)
- [Safety](#safety)
- [Project Structure](#project-structure)
- [Development](#development)
- [Documentation](#documentation)

## Quick Start

```powershell
cd D:\jianzhi\lyuShao\muxdev
python -m pip install -e ".[test]"

muxdev
muxdev start
muxdev setup --check
muxdev setup --yes
muxdev provider detect
muxdev provider doctor codex
muxdev dev "add rate limiting and tests" --provider mock --json
muxdev tasks
```

Run `muxdev start` once to launch the local daemon and Web Dashboard. Task lifecycle commands connect to the daemon API at `http://127.0.0.1:8788` by default, while the Dashboard is served at `http://127.0.0.1:8787`. Automation and CI should prefer `--json`.

## Features

- **Provider capability matrix**: detects `mock`, `codex`, `claude-code`, `qwen`, `kimi`, `trae`, and `antigravity`.
- **Safe static probing**: uses version/help/doctor style commands; no model calls during discovery.
- **Provider install and account guidance**: dry-run install plans plus signup/login docs.
- **Profile/gate main path**: `setup` plus `dev/fix/review/test` with `solo`, `pair`, `squad`, and `ci` profiles.
- **Staged workflow runtime**: plan, approval, code, test, review, fix loops.
- **Role-based providers**: assign different CLIs to `plan`, `code`, `test`, `review`, `secure`, and `docs` roles.
- **Skill Engine**: auto-discovers standard `SKILL.md` packages and injects them by task, role, and binding policy.
- **Daemon-owned state**: a single local SQLite state store under `~/.muxdev/data/muxdev.sqlite`, with task artifacts, traces, reports, diffs, and session logs under `~/.muxdev/data/runs/`.
- **Approvals and safety policy**: plan/write/shell/merge approvals, shell policy checks, and secret redaction.
- **Trusted delivery evidence**: stage contracts, role result contracts, evidence bundles, hash-chained ledgers, blind validator panels, approval subject hashes, and stage snapshot rollback.
- **TUI and REPL**: daemon-backed terminal dashboard, slash commands, provider status, approvals, reports, and trace views.
- **Workflow plugins and flows**: agtx-inspired plugin catalog and CAO-inspired manually triggered flow definitions.
- **MCP surface**: provider detection, workspace search, RAG query, workflow plugin, and flow listing tools.
- **TOML-first configuration**: `config.toml` and optional `skills.toml` are the default user-facing files; legacy YAML config is still read for compatibility.

## Installation

Requirements:

- Python 3.11+
- Git for worktree-backed runs
- Optional provider CLIs such as Codex CLI, Claude Code, Qwen Code, or Kimi Code
- Optional `tmux` for native attach workflows on POSIX systems

Install from this repository:

```bash
python -m pip install -e ".[test]"
```

Verify:

```bash
muxdev --version
python -m muxdev --version
python scripts/verify_cross_platform.py
```

## Provider Setup

List known providers and capability states:

```powershell
muxdev provider detect
muxdev provider detect --json
muxdev provider doctor codex --json
```

Install plans are dry-run by default:

```powershell
muxdev provider install codex
muxdev provider install qwen --json
muxdev provider install qwen --execute
```

Show account and login guidance:

```powershell
muxdev provider account codex
muxdev provider account qwen --json
```

Built-in providers:

| Provider | Role | Install | Login |
| --- | --- | --- | --- |
| `mock` | Deterministic local provider | built in | none |
| `codex` | OpenAI Codex CLI | `npm install -g @openai/codex` | `codex login` |
| `claude-code` | Anthropic Claude Code | `npm install -g @anthropic-ai/claude-code` | first `claude` run |
| `qwen` | Qwen Code | `npm install -g @qwen-code/qwen-code@latest` | `qwen auth` |
| `kimi` | Kimi Code | `npm install -g @moonshot-ai/kimi-code@latest` | `kimi login` |
| `trae` | Manual/experimental | manual | product UI |
| `antigravity` | Manual/experimental | manual | product UI |

## Usage

### Start The Daemon

```powershell
muxdev start
muxdev serve --status
muxdev dashboard
muxdev serve --stop
```

`muxdev serve` runs the service in the foreground. `muxdev serve --daemon` and `muxdev start` launch it in the background with a PID file and log under `~/.muxdev/data/`.

### Setup

```powershell
muxdev setup --check
muxdev setup --yes
muxdev setup --project --yes
muxdev config
muxdev config source
muxdev config set gate strict --project
```

`setup` detects local provider CLIs, caches the result under `~/.muxdev/cache/providers.json`, and writes a minimal `config.toml`. By default muxdev only creates `config.toml`; advanced preset files are created only by `muxdev setup --full` or `muxdev preset copy/edit`.

### Run A Workflow

```powershell
muxdev dev "add rate limiting and tests" --provider mock
muxdev fix "fix a small bug" --provider codex --json
muxdev review
muxdev test
muxdev tasks
muxdev status latest
```

Assign providers per role:

```powershell
muxdev dev "ship the task" --role plan=codex --role code=qwen --role test=mock --role review=codex
muxdev dev "harden auth" -p squad -g strict -s review=security-review
```

Each task gets a `task_id`/`run_id`. The daemon writes state under `~/.muxdev/data/`, including:

- `muxdev.sqlite`
- `trace.jsonl`
- `workflow.yaml`
- `task.md`
- `diff.patch`
- `final_report.md`
- `session/*.log`

### Approvals

```powershell
muxdev dev "needs plan review" --provider mock --require-approval plan --json
muxdev approvals --status pending --json
muxdev approve <approval_id>
muxdev deny <approval_id>
muxdev continue latest
```

### Continue, Retry, Skip

```powershell
muxdev continue latest --json
muxdev retry latest --stage review --json
muxdev skip latest --stage review --reason "accepted risk"
```

### Reports, Diffs, Rollback

```powershell
muxdev report latest
muxdev report latest --json
muxdev diff latest
muxdev rollback latest --json
```

Rollback only touches the isolated run worktree.

### TUI And REPL

```powershell
muxdev
muxdev tui latest
muxdev tui latest --json
muxdev repl
```

Inside the TUI, type `/` for commands such as `/run`, `/continue`, `/status`, `/tasks`, `/approvals`, `/approve`, `/deny`, `/report`, `/diff`, `/dashboard`, and `/quit`.

### Skills

```powershell
muxdev skill add ./.agents/skills/api-review
muxdev skill list --json
muxdev skill show api-review
muxdev skill bind review api-review
muxdev skill sync
muxdev skill doctor
muxdev skill inject api-review
```

Recommended skill directories are `~/.agents/skills/` and `<repo>/.agents/skills/`. muxdev also scans `.muxdev/skills`, `.claude/skills`, `skills`, and compatible global skill directories. `skill install` is kept for the older workspace registry and native export tests.

### Presets And Plugins

```powershell
muxdev preset list
muxdev preset show profile squad
muxdev preset copy workflow dev --project
muxdev plugin add local-plugin --json
muxdev plugin list
```

Plugin commands are a safe local registry in this phase. They do not download code, execute hooks, or auto-enable MCP tools.

### Search, RAG, Metrics

```powershell
muxdev search "TODO" --json
muxdev rag index --json
muxdev rag query "approval policy" --json
muxdev metrics latest --json
muxdev metrics latest --prometheus
muxdev trace chrome latest --json
muxdev evidence verify latest --json
```

`rag index` uses deterministic local embeddings by default. Set `MUXDEV_EMBEDDING_COMMAND` to delegate embeddings to an external command, or `MUXDEV_EMBEDDING_FILE` for deterministic file-backed tests.

### Task Dashboard

The daemon serves the unified task dashboard at `http://127.0.0.1:8787`. The page shows all tasks, workflow timelines, role/provider assignments, approvals, artifacts, test/review results, usage, and recent trace events.

```powershell
muxdev dev "ship the task" --provider mock --json
muxdev dashboard
```

The dashboard calls the daemon API for approvals, stop/continue actions, diff/report access, and live event updates.

## Configuration

Inspect the merged configuration:

```powershell
muxdev config
muxdev config source
muxdev config check
muxdev config paths
muxdev config show --json
muxdev config validate --json
```

TOML runtime load order:

1. Built-in muxdev runtime defaults
2. Global config: `~/.muxdev/config.toml`
3. Project config: `<repo>/.muxdev/config.toml`
4. Task file from `muxdev dev -f task.toml`

Legacy YAML load order, still used by provider/workflow defaults and compatibility commands:

1. Bundled defaults in `muxdev.config.defaults`
2. User config
   - Windows: `%APPDATA%\muxdev\config.yaml`
   - Linux/macOS: `~/.config/muxdev/config.yaml`
3. Project config: `.muxdev/config.yaml`
4. Extra config from `MUXDEV_CONFIG`

`config.toml` controls profile, gate, role-to-provider mappings, and CLI fallback. Legacy YAML can still extend or override providers, account docs, installer plans, runtime command templates, workflows, workflow plugins, command dialects, runtime paths, and TUI commands.

Minimal project config:

```toml
version = 1
profile = "squad"
gate = "strict"

[roles]
plan = "claude-code"
code = "codex"
test = "qwen"
review = "codex"
```

Optional skill policy:

```toml
version = 1
dirs = ["./.agents/skills", "~/.agents/skills"]
auto = true
sync = "auto"

[bind]
review = ["api-review", "security-review"]
```

See [docs/configuration.md](docs/configuration.md) for the full schema guide.

## Workflow Plugins And Flows

Workflow plugins are lightweight, spec-driven command catalogs inspired by agtx.

```powershell
muxdev workflow plugins --json
muxdev workflow plugin spec-lite --json
muxdev workflow render spec-lite --phase planning --provider codex --task "ship the task" --json
```

Codex command dialects are translated from canonical slash commands:

```text
/spec-lite:plan ship it
$spec-lite-plan ship it
```

Flows are local scheduled-run definitions inspired by CAO. M0-M7 keeps execution explicit:

```powershell
muxdev flow add daily-review --schedule "0 9 * * *" --task "review open changes" --provider mock --json
muxdev flow list --json
muxdev flow run daily-review --json
muxdev flow run daily-review --execute --json
```

## MCP And Integrations

```powershell
muxdev mcp manifest --json
muxdev mcp serve --request '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
muxdev dashboard --json
muxdev graph export --json
muxdev deep-agent plan "ship the feature" --json
muxdev session start mock --command "python -m http.server 9999" --json
muxdev session list --json
```

Current MCP tools include:

- `provider.detect`
- `workspace.search`
- `rag.query`
- `workflow.plugins`
- `workflow.render`
- `flow.list`

## Safety

```powershell
muxdev policy shell "pytest" --json
muxdev policy shell "rm -rf /" --json
muxdev evidence verify latest --json
muxdev rollback latest --to-stage implement --json
```

Safety defaults:

- Provider detection avoids model calls.
- Provider installs are dry-run unless `--execute` is passed.
- Merge is dry-run unless `--execute` is passed.
- Approval gates can pause plan, write, shell, and merge actions.
- Approval subjects include stable hashes for plans, patches, validators, and policy state; stale approved subjects require a new approval.
- Completed runs write a hash-chained ledger, evidence bundles, role contracts, and a blind validator result before final merge approval.
- Common `sk-*` and `Bearer *` secrets are redacted from traces, reports, and archived provider output.

## Project Structure

```text
muxdev/
  pyproject.toml
  README.md
  .github/
  docs/
  examples/
  plugins/
  scripts/
  src/
    muxdev/
      api/           MCP and local web dashboard surfaces
      cli/           Typer command app and command wiring
      clients/       subprocess, session, and stream clients
      config/        dynamic config loader, defaults, accounts, installers
      core/          platform utilities
      daemon/        local API server, process manager, and task manager
      models/        domain models
      providers/     provider registry and runtime adapters
      runtime/       supervisor runtime and worktree preparation
      services/      flows, RAG, skills, reports, workflow plugins
      storage/       SQLite blackboard and JSONL trace readers
      ui/            Rich rendering, REPL, TUI dashboard
      workflows/     workflow parser and DAG helpers
  skills/
  tests/
```

## Development

Run tests:

```bash
python -m pytest
```

Run the cross-platform verification suite:

```bash
python scripts/verify_cross_platform.py
```

The suite covers provider detection, install/account plans, dynamic configuration, workflow execution, blackboard records, approvals, resume/retry/skip, trace/report/metrics output, role provider assignment, skills, REPL/TUI behavior, MCP, RAG, and Windows/POSIX command handling.

## Documentation

- [Architecture](docs/architecture.md)
- [Best practices](docs/best_practices.md)
- [Configuration](docs/configuration.md)
- [P0 acceptance ready guide](docs/p0_acceptance_ready.md)
- [P1 trusted delivery ready guide](docs/p1_trusted_delivery_ready.md)
- [Source walkthrough](docs/source_walkthrough.md)

## References

This README structure is inspired by the concise quickstart and local-agent positioning in [OpenAI Codex CLI](https://github.com/openai/codex/blob/main/README.md), and by the feature navigation, workflow plugins, MCP, and configuration sections in [agtx](https://github.com/fynnfluegge/agtx).
