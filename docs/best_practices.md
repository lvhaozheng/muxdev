# muxdev 最佳实践

本文面向日常使用和维护 muxdev 的开发者，覆盖最新 P0-P4 实现后的推荐路径。

## 推荐日常路径

首次使用：

```powershell
muxdev setup --check
muxdev setup --yes
muxdev start
muxdev dev "add a small feature" --provider mock --json
muxdev status latest
muxdev dashboard
```

常用任务入口：

```powershell
muxdev design "design the auth migration"
muxdev dev "implement the auth migration"
muxdev fix "fix failing login test"
muxdev refactor "split billing module" --parallel
muxdev review
muxdev test
muxdev ci "triage CI failure"
muxdev why "why is this task blocked?"
```

不再推荐使用旧入口：

```text
run      -> dev
resume   -> continue
web      -> dashboard
account  -> provider account
install  -> provider install
doctor   -> provider doctor
```

## Daemon

启动后台服务：

```powershell
muxdev start
```

前台启动：

```powershell
muxdev serve
```

查看状态：

```powershell
muxdev serve --status
```

重启：

```powershell
muxdev serve --restart
```

默认端口：

- Dashboard: `127.0.0.1:8787`
- API: `127.0.0.1:8788`

如果 TUI 出现 `404 {"detail":"Not Found"}`，通常说明旧 daemon 仍在运行，或当前 CLI 连接到了旧 API。先执行：

```powershell
muxdev serve --restart
muxdev serve --status
```

再启动：

```powershell
muxdev tui
```

## Provider

先探测，再绑定：

```powershell
muxdev provider detect
muxdev provider doctor codex --json
```

不要在 config 中长期绑定尚未安装、未登录或状态不稳定的 provider。需要 smoke test 时优先用 `mock`：

```powershell
muxdev dev "smoke task" --provider mock --json
```

安装 provider 默认 dry-run：

```powershell
muxdev provider install codex
```

确认计划后再执行：

```powershell
muxdev provider install codex --execute
```

推荐按角色绑定 provider，而不是所有阶段都固定一个 provider：

```toml
[roles]
plan = "claude-code"
code = "codex"
test = "mock"
review = "codex"
```

## Provider Action

Provider Action 表示外部 provider CLI/session 需要人工处理，不是 muxdev 审批。

常见类型：

- `cli_confirmation`
- `auth_required`
- `rate_limit`
- `provider_blocked`
- `idle_timeout`

查看待处理项：

```powershell
muxdev actions --status pending --json
```

处理流程：

1. 查看 `prompt_text`、`options_json` 和 `attach_command`。
2. 进入对应 provider CLI/session 完成登录、确认或限流处理。
3. 回到 muxdev 标记 handled。
4. 继续任务。

```powershell
muxdev action handled <action_id>
muxdev continue latest
```

如果确认该项不需要继续处理：

```powershell
muxdev action dismiss <action_id>
```

重要约束：Dashboard/TUI 不会替你向 provider CLI 输入 yes/no。它们只展示提示、选项、日志路径和 attach 指令。

## Approvals

Approvals 是 muxdev 的策略审批，例如 plan/write/shell/merge。

查看：

```powershell
muxdev approvals --status pending --json
```

批准或拒绝：

```powershell
muxdev approve <approval_id>
muxdev deny <approval_id>
```

然后继续：

```powershell
muxdev continue latest
```

Provider Action 和 Approval 要分开处理。任务卡片、TUI 和 Dashboard 会分别显示 pending approvals 与 pending provider actions 的数量。

## Gate 与 Profile

本地普通开发：

```powershell
muxdev dev "add cache" -g safe
```

高风险修改：

```powershell
muxdev dev "rewrite storage layer" -g strict
```

CI 或非交互场景：

```powershell
muxdev ci "smoke test" --provider mock --json
```

`ci` gate 下如果触发人工审批，任务应该进入 blocked，而不是无限等待交互。

自动路由可以通过 flag 覆盖：

```powershell
muxdev dev "tiny typo fix" --simple
muxdev dev "safe module change" --safe
muxdev dev "cross-module migration" --deep
muxdev dev "parallel module migration" --parallel
```

## 设计先行

需要先出设计时：

```powershell
muxdev design "design payment retry strategy" --provider mock --json
```

`design` 会走设计阶段和 memory context，不要求立刻写代码。适合：

- 需求澄清
- 架构调整
- 风险评估
- 跨模块拆分
- 多仓编排前置规划

在 TUI 中也可以直接使用：

```text
/design design payment retry strategy
/dev implement payment retry strategy
```

## Memory

查看状态：

```powershell
muxdev memory status --json
```

查询：

```powershell
muxdev memory query "auth" --json
```

从最新 run 的 evidence 提出候选记忆：

```powershell
muxdev memory propose latest --json
```

批准：

```powershell
muxdev memory approve <mem_id>
```

P4 后建议定期检查矛盾：

```powershell
muxdev memory contradictions --json
muxdev memory quarantine-auto --json
```

记忆最佳实践：

- 只把稳定事实、架构决策、测试约定、交付偏好放入 memory。
- 不把一次性报错、临时猜测、过期 workaround 作为 accepted memory。
- 有冲突时先 quarantine，确认后再重写或批准新记忆。

## Evidence 与可信交付

每次交付重点看：

```powershell
muxdev report latest
muxdev diff latest
```

Dashboard task detail 中应检查：

- role contracts
- role results
- evidence snapshots
- hash ledger
- Blind Validator
- semantic merge review
- rollback snapshot
- provider attempts
- provider actions

如果 Blind Validator reject，优先看：

```text
validation/blind_validator_panel.json
```

如果 semantic merge 标记风险，优先看 Dashboard 的 Semantic Merge Reviews 或：

```powershell
muxdev parallel conflicts --status open --json
```

## 并行与语义合并

使用 parallel 前，尽量让设计阶段产出明确的 planned writes：

```powershell
muxdev refactor "split auth modules" --parallel --provider mock --json
```

手动检查 planned write 冲突：

```powershell
muxdev parallel conflicts --file writes.json --json
```

持久化到本地 ecosystem store：

```powershell
muxdev parallel conflicts --file writes.json --record --json
```

实践建议：

- 并行适合模块边界清楚的任务。
- 同文件或同 symbol 高风险写入应先拆分任务。
- semantic merge review 通过前，不要把并行产物当成可信最终交付。

## Provider Learning

查看跨 run provider 学习快照：

```powershell
muxdev learning provider --json
muxdev learning provider --role code --json
```

TUI：

```text
/learning
```

provider action、失败 attempt、fallback 会降低相关 provider/role 的可信度；成功 attempt 会提高学习快照。选择 provider 时优先参考 role 维度，而不是只看全局 provider 名称。

## Feedback、CI Rescue 与 Cache

外部反馈进入系统：

```powershell
muxdev feedback add ci_failed "pytest failed in tests/test_login.py" --source github-actions --provider mock --json
```

查看生态状态：

```powershell
muxdev feedback list --json
muxdev cache list --json
```

CI rescue 会把 CI 失败反馈路由到修复任务，并记录 feedback 与 rescue run 关系。CAS cache 会记录可复用反馈事件和路径。

## Skills 与 Plugins

最小 skill 结构：

```text
.agents/skills/api-review/
  SKILL.md
```

显式使用：

```powershell
muxdev dev "review API compatibility" -s review=api-review
```

长期绑定：

```powershell
muxdev skill bind review api-review --project
```

生成 skill lock：

```powershell
muxdev skill lock --json
```

只生成 lock，不写 skill memory：

```powershell
muxdev skill lock --no-memory --json
```

检查：

```powershell
muxdev skill doctor --json
```

Plugin manifest 必须经过安全校验。只包含安全权限的 manifest 才会被标为 trusted。

## Multi-Repo

P4 的 multi-repo 是可审计编排计划，不直接跨仓写代码：

```powershell
muxdev multirepo plan "coordinate auth API change" --repo path\to\repo-a --repo path\to\repo-b --mode design --json
muxdev multirepo dev "coordinate auth API change" --repo path\to\repo-a --repo path\to\repo-b --json
```

生成计划后，再进入每个仓库执行对应 `muxdev design/dev`。

## TUI

启动：

```powershell
muxdev
```

或：

```powershell
muxdev tui
```

常用命令：

```text
/design <task>
/dev <task>
/fix <task>
/refactor <task>
/review [task]
/test [task]
/ci <task>
/why <question>
/tasks
/status [task-id]
/approvals
/actions
/action handled <id>
/action dismiss <id>
/parallel
/learning
/continue [task-id]
/dashboard
/quit
```

TUI 适合观察当前任务、等待项和轻量操作。完整 diff/report 仍建议用 CLI 或 Dashboard。

## Dashboard

启动：

```powershell
muxdev dashboard
```

Dashboard 适合：

- 查看所有任务。
- 区分 approvals 与 provider actions。
- 查看 timeline、trace、artifacts。
- 查看 evidence、memory context、role sessions。
- 处理 Mark handled / Dismiss。
- 观察 feedback、CI rescue、CAS cache。
- 查看 parallel conflicts、semantic merge、provider learning。

如果 Dashboard 看不到新面板，优先重启 daemon：

```powershell
muxdev serve --restart
```

## 测试

完整回归：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pytest -q
```

重点回归：

```powershell
python -m pytest tests/test_daemon_client_server.py -q
python -m pytest tests/test_repl_tui_m7.py -q
python -m pytest tests/test_p0_automation_memory.py -q
python -m pytest tests/test_p1_trusted_delivery.py -q
python -m pytest tests/test_p2_runtime_safety_provider.py -q
python -m pytest tests/test_p3_ecosystem_automation.py -q
python -m pytest tests/test_p4_advanced_parallel_learning.py -q
```

Windows 上如果 `.pytest_cache` 写入失败，通常只是权限 warning，不影响功能结果。

## 排障速查

TUI 404：

```powershell
muxdev serve --restart
muxdev serve --status
```

任务卡在 approval：

```powershell
muxdev approvals --status pending --json
muxdev approve <approval_id>
muxdev continue latest
```

任务卡在 provider action：

```powershell
muxdev actions --status pending --json
muxdev action handled <action_id>
muxdev continue latest
```

provider 不可用：

```powershell
muxdev provider detect
muxdev provider doctor <name> --json
```

memory 上下文不符合预期：

```powershell
muxdev memory status --json
muxdev memory contradictions --json
muxdev memory quarantine-auto --json
```

Dashboard 信息缺失：

```powershell
muxdev serve --restart
muxdev dashboard
```
