# muxdev 架构

本文描述当前 muxdev 的实现架构。当前形态可以概括为：

```text
Role-aware, memory-grounded, local-first Agentic SDLC control plane
```

也就是说，muxdev 不是单个 provider CLI 的包装器，而是一个本地 daemon 驱动的任务控制面：CLI/TUI/Dashboard/API 负责提交和观察任务，daemon 负责生命周期，runtime 负责编排 provider、角色、审批、证据、记忆和交付闭环。

## 顶层视图

```text
User
  |
  | muxdev / muxdev tui / muxdev dashboard / HTTP API
  v
CLI, TUI, Dashboard
  |
  | DaemonClient / REST / WebSocket
  v
FastAPI daemon
  |
  | TaskManager owns lifecycle, workers, DB writes
  v
SupervisorRuntime
  |
  | role stages, provider adapters, safety, evidence, memory, merge
  v
Provider CLIs + isolated worktrees + artifacts
  |
  v
Blackboard, memory store, run artifacts, ecosystem store
```

默认端口：

- Dashboard: `http://127.0.0.1:8787`
- API: `http://127.0.0.1:8788`

Dashboard 和 API 由同一套 FastAPI app 提供。CLI/TUI 默认通过 `DaemonClient` 访问 API 端口。

## 目录结构

```text
src/muxdev/
  api/        FastAPI app, MCP JSON-RPC, Dashboard HTML
  cli/        Typer commands, daemon-backed TUI, provider subcommands
  clients/    daemon HTTP client, stream parser
  config/     TOML-first runtime config, profiles, gates, provider setup
  core/       platform helpers, safety policy, redaction
  daemon/     daemon process/server paths and TaskManager
  models/     shared domain dataclasses and status enums
  providers/  provider registry, mock provider, runtime adapters
  runtime/    SupervisorRuntime and isolated worktree support
  services/   automation, design, evidence, feedback, cache, learning, merge
  storage/    SQLite blackboard, memory store, hash ledger, trace
  ui/         Rich render helpers and REPL
  workflows/  workflow parser and DAG helpers
```

旧的顶层 shim 或历史入口已经不再作为主路径使用。新增能力应优先放在 `services/`、`runtime/`、`daemon/`、`api/`、`cli/` 或 canonical storage/config/provider 层，避免恢复旧目录式入口。

## 数据存储

### Daemon Blackboard

daemon 运行状态写入：

```text
~/.muxdev/data/muxdev.sqlite
```

主要表包括：

- `runs`
- `stages`
- `agents`
- `approvals`
- `provider_actions`
- `provider_attempts`
- `artifacts`
- `test_results`
- `review_blockers`
- `usage_records`
- `checkpoints`
- `error_details`
- `hash_ledger`
- `feedback_events`
- `ci_rescues`
- `cache_entries`
- `skill_locks`
- `guardrail_events`
- `parallel_conflicts`
- `semantic_merge_reviews`
- `provider_learning`
- `multi_repo_orchestrations`

CLI 不直接写 daemon DB。daemon 中的 `TaskManager` 是全局状态库的边界。

### Run Artifacts

每个 run 写入：

```text
~/.muxdev/data/runs/<run_id>/
```

常见产物：

- `task.md`
- `workflow.yaml`
- `task_context.json`
- `trace.jsonl`
- `final_report.md`
- `diff.patch`
- `role_contracts/*.json`
- `role_results/*.json`
- `evidence/events.jsonl`
- `evidence/manifest.json`
- `evidence/evaluation.json`
- `validation/blind_validator_panel.json`
- `snapshots/*.json`
- `capsules/*.session_capsule.json`
- `capsules/*.handoff.patch`

### Project Memory

项目级长期记忆写入：

```text
<repo>/.muxdev/memory.sqlite
```

记忆项可以经历 `proposed -> accepted -> archived/quarantined`。当前记忆层包含矛盾检测与自动隔离能力，避免低置信或互相否定的记忆继续污染上下文。

### Ecosystem Store

本地非 daemon 生态命令写入：

```text
<repo>/.muxdev/ecosystem.sqlite
```

例如 feedback、CAS cache、provider learning、parallel conflict、本地 multi-repo plan 等。daemon API 场景则写 daemon DB。

## 任务生命周期

以 `muxdev dev "add feature" --provider mock --json` 为例：

```text
cli/main.py
  -> resolve_task_request()
  -> resolve_active_skills()
  -> DaemonClient.submit_task()
  -> POST /api/tasks
  -> TaskManager.submit_task()
  -> Blackboard.create_run()
  -> worker thread
  -> SupervisorRuntime.run()
  -> role stages / providers / approvals / evidence / memory
  -> final report / diff / validator / learning
  -> TaskManager.broadcast()
```

`continue` 路径类似，但会先检查待处理项：

- 存在 pending muxdev approval 时，等待审批。
- 存在 pending provider action 时，返回 `awaiting_provider_action`，不重复启动 worker。
- 待处理项完成后，`SupervisorRuntime.resume()` 继续执行。

## Runtime 分层

`runtime/supervisor.py::SupervisorRuntime` 是实际执行器。核心职责：

1. 创建 run 目录和隔离 worktree。
2. 解析 workflow DAG。
3. 创建 role contract 和 agent/stage 记录。
4. 绑定项目 memory context。
5. 调用 provider adapter。
6. 解析 provider stream 和 provider actions。
7. 执行 safety policy、approval gates、budget gates。
8. 记录 provider attempts、session capsules、handoff patch。
9. 写入 role results、evidence、hash ledger。
10. 运行 semantic merge 和 Blind Validator。
11. 生成 final report、diff、rollback snapshot。
12. 更新 provider score 与 provider learning。

`SupervisorRuntime` 支持 daemon 注入：

```python
SupervisorRuntime(
    workspace,
    runs_dir=paths.runs_dir,
    state_db=paths.db_path,
    worktrees_root=paths.worktrees_dir,
    write_dashboards=False,
)
```

这保证了 runtime 可以复用本地执行能力，同时由 daemon 统一持久化任务状态。

## Approval 与 Provider Action

muxdev 将两类人工介入分开处理。

### Approvals

Approvals 是 muxdev 自己的策略审批，例如：

- `plan`
- `write`
- `shell`
- `merge`
- `external`

相关入口：

```powershell
muxdev approvals --status pending --json
muxdev approve <approval_id>
muxdev deny <approval_id>
```

API：

- `GET /api/approvals`
- `POST /api/approvals/{approval_id}/approve`
- `POST /api/approvals/{approval_id}/deny`

### Provider Actions

Provider Actions 是外部 provider CLI/session 阻塞，例如：

- `cli_confirmation`
- `auth_required`
- `rate_limit`
- `provider_blocked`
- `idle_timeout`

它们不再伪装成 external approval。系统会保存 prompt、可解析选项、transcript/chunks 路径和 attach command。

相关入口：

```powershell
muxdev actions --status pending --json
muxdev action handled <action_id>
muxdev action dismiss <action_id>
```

API：

- `GET /api/provider-actions`
- `GET /api/tasks/{run_id}/provider-actions`
- `POST /api/provider-actions/{action_id}/handled`
- `POST /api/provider-actions/{action_id}/dismiss`

设计约束：v1 不从 Dashboard/TUI 直接向 provider CLI 输入 yes/no。用户先进入对应 attach/session 处理，再回到 muxdev 标记 handled 并 continue。

## 能力层

### 自动 + 角色 + 设计 + 记忆

主要模块：

- `services/automation.py`
- `services/design.py`
- `storage/memory.py`
- `config/runtime.py`

能力：

- intent resolver
- auto flow selector
- role topology compiler
- simple/safe/deep/parallel/ci 自动路由
- `design` 命令与 dev 前设计产物
- 项目 memory 最小闭环

### 可信交付闭环

主要模块：

- `services/evidence.py`
- `storage/ledger.py`
- `services/reports.py`

能力：

- role output contract
- Evidence v2 event stream、manifest、evaluation
- hash ledger
- stage snapshot
- rollback metadata
- Blind Validator Panel

### 运行时安全与 Provider 可信化

主要模块：

- `clients/stream.py`
- `services/session_capsules.py`
- `services/provider_scores.py`

能力：

- provider stream 解析
- provider action 持久化
- provider attempt 记录
- role fallback
- read-only provider gate
- session capsule 和 handoff patch
- provider score

### 生态与自动化闭环

主要模块：

- `services/feedback.py`
- `services/cas_cache.py`
- `services/skills/`
- `services/skill_engine.py`
- `services/skill_lock.py`
- `api/mcp.py`

能力：

- feedback router
- CI rescue
- CAS cache
- Skill Lock 和 Skill Memory
- Skills-first extension governance
- MCP guardrail 工具
- Dashboard/TUI 生态可见性

### 并行、语义合并、学习与多仓编排

主要模块：

- `services/advanced_parallel.py`
- `services/semantic_merge.py`
- `services/provider_learning.py`
- `services/multirepo.py`
- `storage/memory.py`

能力：

- conflict-aware parallel-squad
- semantic merge reviewer
- cross-run provider learning
- memory contradiction detection
- memory quarantine automation
- multi-repo orchestration planning

## API Surface

核心任务 API：

- `GET /api/health`
- `GET /api/daemon/status`
- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/continue`
- `POST /api/tasks/{task_id}/stop`
- `GET /api/tasks/{task_id}/diff`
- `GET /api/tasks/{task_id}/report`
- `POST /api/tasks/{task_id}/rollback`
- `GET /api/tasks/{task_id}/attach-command`

交付与等待项：

- `GET /api/approvals`
- `POST /api/approvals/{approval_id}/approve`
- `POST /api/approvals/{approval_id}/deny`
- `GET /api/provider-actions`
- `GET /api/tasks/{task_id}/provider-actions`
- `POST /api/provider-actions/{action_id}/handled`
- `POST /api/provider-actions/{action_id}/dismiss`

生态、学习与并行：

- `POST /api/feedback`
- `GET /api/ecosystem`
- `GET /api/provider-scores`
- `GET /api/learning/provider`
- `GET /api/parallel-conflicts`
- `GET /api/tasks/{task_id}/parallel-conflicts`
- `GET /api/semantic-merge-reviews`
- `GET /api/tasks/{task_id}/semantic-merge-reviews`
- `GET /api/multi-repo/orchestrations`
- `POST /api/multi-repo/plan`
- `GET /api/memory/contradictions`
- `POST /api/memory/quarantine-auto`

事件：

- `WS /events`
- `WS /api/events`

## CLI/TUI/Dashboard

### CLI

`cli/main.py` 是命令注册中心。主路径命令包括：

- `design`
- `dev`
- `fix`
- `refactor`
- `review`
- `test`
- `ci`
- `why`
- `tasks`
- `status`
- `continue`
- `stop`
- `report`
- `diff`
- `rollback`
- `approvals`
- `actions`
- `memory`
- `parallel`
- `learning`
- `multirepo`
- `feedback`
- `cache`
- `skill`
- `plugin`
- `provider`

Provider 子命令在 `cli/providers.py` 中，负责 detect/list/doctor/account/install。

### TUI

`cli/tui.py` 是 daemon-backed TUI。它不做实时 provider detect，避免 Windows 上外部 CLI 反复弹窗。当前支持：

- `/design`
- `/dev`
- `/fix`
- `/refactor`
- `/review`
- `/test`
- `/ci`
- `/why`
- `/tasks`
- `/status`
- `/approvals`
- `/actions`
- `/action handled <id>`
- `/action dismiss <id>`
- `/parallel`
- `/learning`
- `/continue`

当当前任务存在 provider action 时，TUI 会显示 prompt 摘要、选项和 attach 指令，并提示用户去 provider CLI/session 处理。

### Dashboard

`api/web.py::render_live_dashboard_html` 输出 live dashboard。Dashboard 展示：

- task summary
- timeline
- approvals
- Provider Actions
- provider attempts
- session capsules
- role sessions
- evidence
- memory context
- feedback / CI rescue / CAS cache
- skill lock / plugin manifest
- guardrail events
- parallel conflicts
- semantic merge reviews
- provider learning
- artifacts, tests, blockers, usage, trace

Dashboard 的 Provider Actions 面板只提供 `Mark handled`、`Dismiss` 和任务操作，不提供误导性的 approve/deny。

## 配置模型

配置是 TOML-first：

```text
builtin < global < project < task < CLI options
```

常用文件：

```text
~/.muxdev/config.toml
<repo>/.muxdev/config.toml
~/.muxdev/skills.toml
<repo>/.muxdev/skills.toml
```

主实现：

- `config/runtime.py`: runtime config merge 和 task request resolve
- `config/loader.py`: 历史 YAML provider/workflow/path 兼容
- `services/skill_engine.py`: skill discovery, binding, trust, auto-match

## 扩展原则

新增能力建议遵循：

- 状态写入经过 `TaskManager` 或明确的本地 ecosystem store。
- CLI 只做参数解析、格式化输出和 daemon client 调用。
- Provider 阻塞用 Provider Action，不用 muxdev approval 伪装。
- 能力实现放 service 层，runtime 只编排。
- Dashboard/TUI 只展示和触发 muxdev 状态操作，不替用户向外部 provider CLI 输入确认。
- 涉及交付可信度的能力必须写入证据、hash 或可追踪 artifact。

## 测试布局

按能力定位：

- `python -m pytest -q -k "auto_request or design_runtime"`
- `python -m pytest -q -k "trusted_delivery or approval_subject_drift"`
- `python -m pytest -q -k "read_only_stage or provider_action_writes"`
- `python -m pytest -q -k "feedback_router or skill_lock or mcp_guardrail"`
- `python -m pytest -q -k "parallel_conflict or provider_learning or multi_repo"`
- `python -m pytest -q -k "product_experience or evidence_v2 or skill"`
- `tests/test_daemon_client_server.py`
- `tests/test_repl_tui_m7.py`
- `tests/test_strategy_main_path.py`
- `tests/test_runtime_m1_m4.py`
- `tests/test_stream_workflow_safety.py`
- `tests/test_structure.py`

推荐回归：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pytest -q
```
