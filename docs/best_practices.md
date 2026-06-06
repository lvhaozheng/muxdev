# muxdev 最佳实践

本文给出基于当前实现的日常使用、配置、扩展和排障建议。

## 日常主路径

推荐从这条路径开始：

```powershell
muxdev setup --check
muxdev setup --yes
muxdev start
muxdev dev "描述你的任务" --provider mock --json
muxdev tasks
muxdev status latest
muxdev dashboard
```

开发类任务优先使用：

- `muxdev dev "<task>"`
- `muxdev fix "<issue>"`
- `muxdev review`
- `muxdev test`

不要再使用旧入口：

- `muxdev run`
- `muxdev resume`
- `muxdev web`
- `muxdev account`
- `muxdev install`
- `muxdev doctor`

替代关系：

```text
run      -> dev
resume   -> continue
web      -> dashboard
account  -> provider account
install  -> provider install
doctor   -> provider doctor
```

## Daemon 使用

### 启动

```powershell
muxdev start
```

或前台启动：

```powershell
muxdev serve
```

查看状态：

```powershell
muxdev serve --status
```

停止：

```powershell
muxdev serve --stop
```

### 端口约定

保持默认端口，除非有明确冲突：

- Dashboard: `127.0.0.1:8787`
- API: `127.0.0.1:8788`

如果改端口，CLI 命令需要配合 `--host --port`，或设置：

```powershell
$env:MUXDEV_API_URL = "http://127.0.0.1:8788"
```

### Windows 注意事项

- daemon 后台启动使用隐藏 subprocess，避免反复弹 terminal。
- TUI 渲染不做 provider 实时探测，避免外部 CLI 引发窗口抖动。
- 运行测试建议设置：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
```

如果工作区里旧 `__pycache__` 删除失败，多半是 ACL 问题，不影响源码运行；用管理员权限清理即可。

## 配置策略

### 保持配置最小

默认只维护两个文件：

```text
~/.muxdev/config.toml
<repo>/.muxdev/config.toml
```

只有需要 skill 策略时再创建：

```text
~/.muxdev/skills.toml
<repo>/.muxdev/skills.toml
```

不要默认生成 roles/workflows/gates/profiles/presets 目录。只有需要高级 preset 时再运行：

```powershell
muxdev setup --full --project --yes
```

### 用 project config 固化团队约定

团队仓库推荐把以下内容放到 `<repo>/.muxdev/config.toml`：

```toml
profile = "squad"
gate = "safe"

[roles]
plan = "claude-code"
code = "codex"
test = "mock"
review = "codex"
```

个人机器上的 provider fallback 和本地 CLI 路径放在 `~/.muxdev/config.toml`。

### 用 task file 做一次性覆盖

复杂任务建议写 `task.toml`：

```toml
task = "重构 auth 模块并补测试"
profile = "pair"
gate = "strict"

[roles]
code = "codex"
review = "claude-code"

[[skill]]
role = "review"
name = "security-review"
```

提交：

```powershell
muxdev dev -f task.toml --json
```

## Provider 最佳实践

### 先探测，后绑定

```powershell
muxdev provider detect
muxdev provider doctor codex --json
```

不要在 config 里绑定尚未安装或不可用的 provider。mock provider 永远可用，适合测试和 smoke run。

### 安装默认 dry-run

```powershell
muxdev provider install codex
```

确认计划后再执行：

```powershell
muxdev provider install codex --execute
```

### 按角色分配 provider

常见组合：

```toml
[roles]
plan = "claude-code"
code = "codex"
test = "qwen"
review = "codex"
```

如果只想用一个 provider，直接传：

```powershell
muxdev dev "task" --provider codex
```

## Gate 和审批

### 本地开发

推荐：

```powershell
muxdev dev "task" -g safe
```

`safe` 会要求 plan/write/shell/merge 审批。

### 高风险改动

使用：

```powershell
muxdev dev "rewrite storage layer" -g strict
```

`strict` 会增加 external gate。

### CI / 非交互

使用：

```powershell
muxdev dev "ci smoke" -p ci -g ci --json
```

`ci` gate 下如果需要人工审批，应进入 blocked，不等待交互。

## Skill 最佳实践

### Skill 文件结构

最小结构：

```text
.agents/skills/api-review/
  SKILL.md
```

`SKILL.md` 推荐包含 frontmatter：

```markdown
---
name: api-review
description: Review API compatibility and request/response shape.
keywords: [api, http, compatibility]
---

# API Review

检查路由、状态码、错误体和向后兼容性。
```

### 显式优先

任务强依赖 skill 时，用 `-s` 显式指定：

```powershell
muxdev dev "review API changes" -s review=api-review
```

### 角色绑定

长期策略写入 `skills.toml`：

```powershell
muxdev skill bind review api-review --project
```

### 定期 doctor

```powershell
muxdev skill doctor
```

重点关注：

- 重名 skill
- 缺少 description
- binding 指向不存在的 skill
- disabled/trust 策略是否符合预期

## TUI 和 Dashboard

### TUI

```powershell
muxdev
```

常用 slash command：

```text
/run <task>
/tasks
/status [task-id]
/approvals
/approve <approval-id>
/deny <approval-id>
/continue [task-id]
/report [task-id]
/diff [task-id]
/dashboard
/quit
```

TUI 适合快速查看状态和触发操作，不适合展示完整 diff/report。完整内容用 CLI 或 Dashboard。

### Dashboard

```powershell
muxdev dashboard
```

Dashboard 适合：

- 浏览所有任务。
- 查看 timeline。
- 审批。
- 查看 artifacts。
- 跳转 diff/report。
- 观察 WebSocket 事件更新。

## 本地工具命令

这些命令仍然本地执行，不依赖 daemon task lifecycle：

- `provider`
- `config`
- `preset`
- `skill`
- `plugin`
- `policy`
- `trace`
- `metrics`
- `search`
- `mcp`
- `session`
- `rag`
- `graph`
- `deep-agent`
- `workflow`
- `flow`

修改这些命令时，尽量保持“CLI 只做参数和输出，业务逻辑在 service/config/provider 层”。

## 开发和测试

### 运行测试

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pytest
```

### 针对性测试

```powershell
python -m pytest tests/test_daemon_client_server.py
python -m pytest tests/test_strategy_main_path.py
python -m pytest tests/test_repl_tui_m7.py
```

### 结构回归

旧 shim 被删除后，新增代码不要再导入：

- `muxdev.mcp`
- `muxdev.rag`
- `muxdev.skills`
- `muxdev.web`
- `muxdev.sessions`
- `muxdev.stream`
- `muxdev.workflow`
- `muxdev.reports`
- `muxdev.orchestration`
- `muxdev.safety`
- `muxdev.providers.accounts`
- `muxdev.providers.installers`
- `muxdev.core.models`

使用 canonical imports：

- `muxdev.api.*`
- `muxdev.clients.*`
- `muxdev.config.*`
- `muxdev.core.*`
- `muxdev.models`
- `muxdev.services.*`
- `muxdev.workflows`

## 排障

### TUI 显示 daemon request failed

先检查 daemon：

```powershell
muxdev serve --status
muxdev start
```

如果环境有 HTTP proxy，确认 CLI 使用的是当前实现的 `DaemonClient`，它对 localhost 请求设置了 `trust_env=False`。

### Dashboard 访问不到

检查：

```powershell
muxdev serve --status
muxdev dashboard --json
```

确认 8787/8788 没被占用。

### 任务卡在 awaiting_approval

查看审批：

```powershell
muxdev approvals --status pending
```

批准后继续：

```powershell
muxdev approve <approval_id>
muxdev continue latest
```

### rollback 失败

rollback 只处理 isolated worktree。如果 worktree 不存在，会返回 failed。检查：

```powershell
muxdev status <task-id> --json
```

### provider 不可用

```powershell
muxdev provider detect
muxdev provider doctor <name>
```

缺 CLI 时先 dry-run 安装：

```powershell
muxdev provider install <name>
```
