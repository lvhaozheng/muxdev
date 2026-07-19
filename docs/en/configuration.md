# Configuration

[中文](../cn/configuration.md)

<!-- section:precedence -->
## Precedence

Runtime configuration is TOML-first. Built-ins are merged with global configuration, project `.muxdev/config.toml`, and an optional task file; later scoped values win. Static catalogs use packaged YAML plus user/project YAML overrides and `MUXDEV_CONFIG`. Unknown top-level catalog sections are reported. Retired `profile`, `topology`, and plugin-named workflow configuration are not accepted on the new task path.

<!-- section:runtime -->
## Runtime Options

New tasks use `intent`, `depth`, `workflow`, role-to-Provider overrides, and gates. Depth values are `auto`, `simple`, `safe`, `deep`, `parallel`, or `ci`. Gates are `auto`, `safe`, `strict`, and `ci`; strict/CI gates can require approval for plan, write, shell, merge, or external operations. Set a finite `max_cost_usd` for real Provider work.

```toml
version = 2
gate = "safe"

[automation]
depth = "auto"
allow_parallel = true

[roles]
plan = "codex"
code = "codex"
test = "qwen"
review = "qwen"
```

<!-- section:workflow -->
## Workflows and Templates

Workflow definitions are native DAGs containing stages, roles, dependencies, conditions, approval types, output schemas, write permissions, and bounded loop metadata. Command templates live under the `workflow_templates` catalog key. The old `workflow_plugins` key and `workflow plugin(s)` aliases are intentionally removed.

Inspect without executing:

```powershell
muxdev graph export --workflow dev --json
muxdev workflow templates --json
muxdev workflow template spec-lite --json
```

<!-- section:provider -->
## Providers and Routing

Provider catalog entries define executable commands, prompt transport, timeouts, capability hints, and resume templates. A fixed `--provider` bypasses quality selection but not policy or certification checks. Role overrides use current role names, for example `--role code=codex --role review=qwen`. Mock/Replay are simulation delivery modes and cannot become production evidence by wording alone.

<!-- section:skills -->
## Skills and Context

Skills have metadata, trust state, optional role/stage bindings, and content loaded only when activated. Project Skills override lower-priority locations but do not become trusted automatically. Use `muxdev skill catalog`, `muxdev skill explain <name>`, and `muxdev skill lock`. Memory promotion remains explicit and evidence references are retained.

<!-- section:security -->
## Security-Sensitive Settings

Keep bearer tokens, signing private keys, Provider credentials, transcripts, and local Provider state outside the repository. Bind the API to loopback. Do not loosen sandbox, network, or reviewer requirements because a Provider claims it is safe. Subject-bound waivers should be narrow and expire when fingerprints or task subjects change.

<!-- section:examples -->
## Task Examples

```powershell
muxdev dev "fix parser edge case" --provider mock --safe
muxdev design "design a durable import pipeline" --deep --gate strict
muxdev refactor "split storage boundaries" --parallel --role review=qwen
muxdev dev "implement approved plan" --file .muxdev/task.toml --max-cost-usd 1.00
```

Use [Security and trust](security-and-trust.md) before changing gates and [Operations](operations.md) for daemon paths and backups.
