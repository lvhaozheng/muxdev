# muxdev 配置指南

本文说明当前 muxdev 的 TOML-first 配置体系、任务解析优先级、角色/provider 路由、自动化、记忆、安全门禁，以及 P0-P4 新能力对配置和命令面的影响。

## 配置文件

主配置文件：

```text
~/.muxdev/config.toml
<repo>/.muxdev/config.toml
```

可选 skill 策略文件：

```text
~/.muxdev/skills.toml
<repo>/.muxdev/skills.toml
```

任务级文件：

```text
task.toml
```

也可以通过环境变量指定任务级配置：

```powershell
$env:MUXDEV_TASK_CONFIG = ".muxdev/tasks/refactor-auth.toml"
```

## 合并顺序

runtime config 合并顺序：

```text
builtin < global < project < task < CLI options
```

含义：

- `builtin`: 代码内置默认值。
- `global`: 用户级默认配置。
- `project`: 仓库级约定。
- `task`: 单次任务覆盖。
- `CLI options`: 命令行参数，优先级最高。

skill policy 合并顺序：

```text
builtin skill policy < global skills.toml < project skills.toml < task -s
```

## 环境变量

### `MUXDEV_HOME`

改变 muxdev 全局目录：

```powershell
$env:MUXDEV_HOME = "D:\muxdev-home"
```

影响：

- global config
- provider cache
- daemon data
- daemon logs
- daemon SQLite
- daemon runs/worktrees

### `MUXDEV_API_URL`

改变 CLI/TUI 访问 daemon 的 API base URL：

```powershell
$env:MUXDEV_API_URL = "http://127.0.0.1:8788"
```

`DaemonClient` 对 localhost 请求使用 `trust_env=False`，避免被系统 HTTP proxy 转发。

### `MUXDEV_TASK_CONFIG`

指定任务级 TOML：

```powershell
$env:MUXDEV_TASK_CONFIG = ".muxdev/tasks/refactor-auth.toml"
```

## 初始化

检查但不写文件：

```powershell
muxdev setup --check
```

写全局推荐配置：

```powershell
muxdev setup --yes
```

写项目配置：

```powershell
muxdev setup --project --yes
```

生成高级 preset 文件：

```powershell
muxdev setup --project --yes --full
```

`setup` 会缓存 provider 探测结果：

```text
~/.muxdev/cache/providers.json
```

## 推荐最小配置

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
auto_promote_low_risk = true
require_approval_for = ["architecture_decision", "security_rule", "payment_rule"]
ttl_days = 180
max_items_per_role = 8
redact_secrets = true

[safety]
level = "balanced"
sensitive_paths = ["auth/**", "payment/**", "migrations/**", ".env"]
require_approval_for = ["dependency_change", "network", "security_sensitive", "merge"]
```

## Profiles

| Profile | 目标 | 默认 roles |
| --- | --- | --- |
| `auto` | 交给 Auto Flow Selector | 由任务决定 |
| `solo` | 单角色快速修改 | `code` |
| `pair` | code + review | `code`, `review` |
| `squad` | 默认协作路径 | `plan`, `code`, `test`, `review` |
| `ci` | 非交互 CI 路径 | `plan`, `code`, `test`, `review` |

命令覆盖：

```powershell
muxdev dev "task" -p pair
```

## Gates

| Gate | require approval | 行为 |
| --- | --- | --- |
| `auto` | `[]` | 尽量自动执行 |
| `safe` | `plan`, `write`, `shell`, `merge` | 默认安全模式 |
| `strict` | `plan`, `write`, `shell`, `merge`, `external` | 高风险模式 |
| `ci` | 类似 strict | 需要审批时进入 blocked，不等待交互 |

命令覆盖：

```powershell
muxdev dev "task" -g strict
muxdev dev "task" --require-approval plan --json
```

## P0: 自动化、设计、记忆

Auto Flow Selector 会根据命令、任务文本、仓库信号、敏感路径、memory context 决定：

- intent: `design/dev/fix/refactor/review/test/ci`
- depth: `simple/safe/deep/parallel/ci`
- topology: `solo/pair/squad/parallel-squad/ci`
- roles

常用命令：

```powershell
muxdev design "design persistent memory" --provider mock --json
muxdev dev "small fix" --simple --provider mock --json
muxdev dev "security-sensitive auth change" --deep --provider mock --json
muxdev dev "parallel module migration" --parallel --provider mock --json
muxdev why latest
```

项目 memory 存在：

```text
<repo>/.muxdev/memory.sqlite
```

命令：

```powershell
muxdev memory status
muxdev memory query "auth boundary"
muxdev memory propose latest
muxdev memory approve mem_xxxxx
```

## P1: 可信交付

P1 不需要额外配置文件，runtime 默认写入：

- `contracts/*.stage_contract.json`
- `contracts/*.role_result.json`
- `evidence/*.evidence.json`
- `ledger.jsonl`
- `snapshots/*.patch`
- `validation/blind_validator_panel.json`
- approval `subject_hash` 和 `subject_json`

命令：

```powershell
muxdev dev "trusted delivery smoke" --provider mock --gate auto --json
muxdev evidence verify latest --json
muxdev rollback latest --to-stage implement --json
```

## P2: 运行时安全和 Provider 稳定性

Provider Action 与 muxdev approval 分离：

```powershell
muxdev actions --status pending --json
muxdev attach <run_id> --agent code
muxdev action handled <action_id>
muxdev action dismiss <action_id>
muxdev continue <run_id>
```

Provider scoring：

```powershell
muxdev provider score --json
muxdev provider score --role code --json
```

P2 相关黑板表：

- `provider_actions`
- `provider_attempts`
- `session_capsules`

## P3: 生态与自动化

Feedback Router 和 CI Rescue：

```powershell
muxdev feedback add ci_failed "pytest failed in tests/test_login.py" --source github-actions --provider mock --json
muxdev feedback list --json
muxdev ci rescue "npm test failed on auth flow" --source github-actions --provider mock --json
```

CAS cache：

```powershell
muxdev cache list --json
```

Skill Lock：

```powershell
muxdev skill lock --json
muxdev skill lock --no-memory --json
```

Safe Plugin Manifest：

```powershell
muxdev plugin validate path\to\plugin --json
muxdev plugin add path\to\plugin --json
```

MCP Guardrail：

```powershell
muxdev mcp manifest --json
muxdev mcp serve --request '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

P3 相关黑板表：

- `feedback_events`
- `ci_rescues`
- `cache_entries`
- `skill_locks`
- `plugin_manifests`
- `guardrail_events`

## P4: 高级并行与长期学习

Conflict-aware parallel-squad：

```powershell
muxdev parallel conflicts --file writes.json --json
muxdev parallel conflicts --status open --json
```

Semantic Merge Reviewer：

- 每次最终 diff 会写 `validation/semantic_merge_review.json`。
- unresolved conflict markers 会触发 `semantic_merge_reject`。

Provider Learning：

```powershell
muxdev learning provider --json
muxdev learning provider --role code --json
```

Memory contradiction 和自动隔离：

```powershell
muxdev memory contradictions --json
muxdev memory quarantine-auto --json
```

Multi-repo orchestration：

```powershell
muxdev multirepo plan "coordinate API change" --repo repo-a --repo repo-b --mode design --json
muxdev multirepo dev "coordinate API change" --repo repo-a --repo repo-b --json
```

P4 v1 生成可审计计划，不自动跨仓提交修改。

P4 相关黑板表：

- `parallel_conflicts`
- `semantic_merge_reviews`
- `provider_learning`
- `multi_repo_orchestrations`

Memory DB 新增：

- `memory_contradictions`

## Role 和 Provider

Canonical roles：

- `lead`
- `plan`
- `code`
- `test`
- `review`
- `secure`
- `docs`
- `requirements`
- `architect`
- `test_strategy`
- `memory_curator`

项目级 role provider：

```toml
[roles]
plan = "claude-code"
code = "codex"
test = "mock"
review = "claude-code"
secure = "claude-code"
docs = "mock"
```

命令行覆盖：

```powershell
muxdev dev "task" --role code=codex --role review=claude-code
```

## Provider fallback

```toml
[cli]
fallback = ["codex", "claude-code", "qwen", "mock"]

[cli.codex]
command = "codex"

[cli.claude-code]
command = "claude"

[cli.qwen]
command = "qwen"

[cli.mock]
command = "mock"
```

如果团队希望默认只用 mock：

```toml
[cli]
fallback = ["mock"]
```

## 任务文件

`task.toml` 支持：

- `task`
- `profile`
- `gate`
- `depth`
- `[roles]`
- `[cli]`
- `[[skill]]`

示例：

```toml
task = "重构 storage trace 并补测试"
profile = "pair"
gate = "strict"
depth = "deep"

[roles]
code = "codex"
review = "claude-code"

[cli]
fallback = ["codex", "mock"]

[[skill]]
role = "review"
name = "api-review"
```

执行：

```powershell
muxdev dev -f task.toml --json
```

## Skill 配置

`skills.toml` 只在需要长期 skill 策略时创建。

```toml
version = 1
dirs = [".agents/skills", "vendor/skills"]
auto = true
sync = "auto"

[bind]
review = ["api-review", "security-review"]
code = ["repo-style"]

[skill.security-review]
trust = "trusted"
disabled = false
auto = true
```

常用命令：

```powershell
muxdev skill add .\.agents\skills\api-review
muxdev skill list --json
muxdev skill show api-review
muxdev skill bind review api-review --project
muxdev skill doctor
muxdev skill lock --json
```

## 配置排障

查看合并结果：

```powershell
muxdev config --json
muxdev config source --json
```

检查 provider：

```powershell
muxdev provider detect --json
muxdev provider doctor codex --json
```

检查 skill：

```powershell
muxdev skill doctor --json
```

如果 task 没有按预期使用 provider，优先检查：

1. CLI 是否传了 `--provider` 或 `--role`。
2. task file 是否覆盖了 `[roles]`。
3. project config 是否覆盖了 global config。
4. provider 是否 ready。
5. fallback 是否把 `mock` 放在了前面。

如果审批行为不符合预期，优先检查：

1. `gate` 是否被 CLI 覆盖。
2. task file 是否指定了 gate。
3. `--require-approval` 是否额外添加了 gate。
4. 是否存在 pending provider action，导致 `continue` 返回 `awaiting_provider_action`。
