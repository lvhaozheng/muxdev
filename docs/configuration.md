# muxdev 配置高阶用法

本文说明当前 TOML-first 配置体系、profile/gate/role/skill 的合并规则，以及常见高级配置写法。

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
$env:MUXDEV_TASK_CONFIG = "task.toml"
```

## 合并顺序

runtime config 合并顺序：

```text
builtin < global < project < task < CLI options
```

含义：

- builtin 是代码中的默认值。
- global 是用户级默认。
- project 是仓库级约定。
- task 是一次性任务覆盖。
- CLI options 拥有最高优先级。

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

### `MUXDEV_API_URL`

改变 CLI/TUI 访问 daemon 的 API base URL：

```powershell
$env:MUXDEV_API_URL = "http://127.0.0.1:8788"
```

优先级高于 `--host --port` 构造出的默认 URL。

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

## Built-in profile

当前内置 profile：

| Profile | 目标 | 默认 roles |
| --- | --- | --- |
| `solo` | 单角色快速修复 | `code` |
| `pair` | code + review | `code`, `review` |
| `squad` | 默认协作路径 | `plan`, `code`, `test`, `review` |
| `ci` | 非交互主路径 | `plan`, `code`, `test`, `review` |

默认：

```toml
profile = "squad"
```

命令覆盖：

```powershell
muxdev dev "task" -p pair
```

## Built-in gate

当前内置 gate：

| Gate | require_approval | 行为 |
| --- | --- | --- |
| `auto` | `[]` | 尽量自动执行 |
| `safe` | `plan`, `write`, `shell`, `merge` | 默认安全模式 |
| `strict` | `plan`, `write`, `shell`, `merge`, `external` | 高风险模式 |
| `ci` | 同 strict | 需要审批时阻塞 |

默认：

```toml
gate = "safe"
```

命令覆盖：

```powershell
muxdev dev "task" -g strict
```

## Role 和 provider

### Canonical roles

当前主角色：

- `lead`
- `plan`
- `code`
- `test`
- `review`
- `secure`
- `docs`

兼容 role alias 会在配置解析时归一化：

| Alias | Canonical |
| --- | --- |
| `supervisor` | `lead` |
| `architect` | `plan` |
| `implementer` | `code` |
| `tester` | `test` |
| `reviewer` | `review` |
| `security` | `secure` |
| `doc_writer` | `docs` |

### 项目级 role provider

```toml
[roles]
plan = "claude-code"
code = "codex"
test = "qwen"
review = "codex"
```

### 命令行覆盖

```powershell
muxdev dev "task" --role code=codex --role review=claude-code
```

### Legacy runtime role 映射

`SupervisorRuntime` 仍使用旧 workflow role 名称执行部分内置 workflow。解析层会把 canonical role 映射到 runtime legacy role：

| Runtime role | Legacy role |
| --- | --- |
| `lead` | `architect` |
| `plan` | `architect` |
| `code` | `implementer` |
| `test` | `tester` |
| `review` | `reviewer` |
| `secure` | `reviewer` |
| `docs` | `implementer` |

因此同时看到 `code` 和 `implementer` provider 映射是正常现象。

## Provider fallback

内置 fallback：

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

选择默认 provider 时会优先选 fallback 中探测为 ready 的 provider。`mock` 始终可作为安全兜底。

如果团队希望默认使用 mock：

```toml
[cli]
fallback = ["mock"]
```

## 任务文件

`task.toml` 支持：

- `task`
- `profile`
- `gate`
- `[roles]`
- `[cli]`
- `[[skill]]`

示例：

```toml
task = "重构 storage trace 并补测试"
profile = "pair"
gate = "strict"

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

命令行参数优先级更高：

```powershell
muxdev dev -f task.toml -g safe --provider mock --json
```

## Config 命令

显示 effective config：

```powershell
muxdev config
muxdev config --json
```

查看来源：

```powershell
muxdev config source
```

检查：

```powershell
muxdev config check
```

设置值：

```powershell
muxdev config set gate strict --project
muxdev config set roles.code codex --project
muxdev config set cli.fallback mock --global
```

打开编辑器：

```powershell
muxdev config edit --project
```

## Preset

列出内置 profile/gate/workflow：

```powershell
muxdev preset list
```

查看：

```powershell
muxdev preset show profile squad
muxdev preset show gate safe
muxdev preset show workflow dev
```

复制到项目：

```powershell
muxdev preset copy gate safe --project
```

编辑：

```powershell
muxdev preset edit workflow dev --project
```

当前 preset copy/edit 是高级用法，默认项目不需要这些文件。

## skills.toml

默认不生成 `skills.toml`。只有需要以下能力时再创建：

- 自定义扫描目录。
- 绑定 role 到 skill。
- disable/enable skill。
- trust 策略。
- auto 策略。

基础示例：

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

## Skill 扫描路径

项目路径：

```text
.agents/skills
.muxdev/skills
.claude/skills
skills
```

全局路径：

```text
~/.agents/skills
~/.muxdev/skills
~/.claude/skills
~/.codex/skills
```

再加上 builtin skills。

同名 skill 使用高优先级版本。

## Skill 激活

显式指定：

```powershell
muxdev dev "review API" -s review=api-review
muxdev dev "write docs" -s docs-style
```

绑定：

```powershell
muxdev skill bind review api-review --project
```

自动匹配依赖 `name`、`description`、`keywords` 是否命中 task 文本。

## Skill 管理命令

安装或添加：

```powershell
muxdev skill add .\.agents\skills\api-review
muxdev skill install builtin:demo
```

列出：

```powershell
muxdev skill list --json
```

查看：

```powershell
muxdev skill show api-review
```

诊断：

```powershell
muxdev skill doctor
```

策略：

```powershell
muxdev skill disable api-review --project
muxdev skill enable api-review --project
muxdev skill trust api-review trusted --project
muxdev skill auto api-review false --project
```

导出：

```powershell
muxdev skill export api-review --output .\.muxdev\exports\api-review
```

## 完整示例

### 个人全局配置

`~/.muxdev/config.toml`：

```toml
version = 1
profile = "squad"
gate = "safe"

[cli]
fallback = ["codex", "claude-code", "mock"]

[cli.codex]
command = "codex"

[cli.claude-code]
command = "claude"

[roles]
code = "codex"
review = "codex"
```

### 项目配置

`<repo>/.muxdev/config.toml`：

```toml
version = 1
profile = "pair"
gate = "strict"

[roles]
code = "codex"
review = "claude-code"
test = "mock"
```

### 项目 skills

`<repo>/.muxdev/skills.toml`：

```toml
version = 1
dirs = [".agents/skills"]
auto = true

[bind]
review = ["api-review"]
test = ["pytest-style"]
```

### 执行任务

```powershell
muxdev dev "重构 API response shape 并补兼容测试" --json
```

如果需要临时降低 gate：

```powershell
muxdev dev "只更新 README" -g auto --provider mock --json
```

## 配置排障

查看合并结果：

```powershell
muxdev config --json
```

查看来源：

```powershell
muxdev config source --json
```

检查 provider：

```powershell
muxdev provider detect --json
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
5. fallback 是否把 `mock` 放在前面。

如果审批行为不符合预期，优先检查：

1. `gate` 是否被 CLI 覆盖。
2. task file 是否指定了 gate。
3. `--require-approval` 是否额外添加了 gate。
4. `ci` gate 是否设置了 `ci_block_on_approval`。
