# muxdev 源码导览

本文按“用户命令如何落到代码”的顺序导览当前实现，面向需要维护或扩展 muxdev 的开发者。

## 包入口

- `src/muxdev/__main__.py`: 支持 `python -m muxdev`。
- `src/muxdev/cli/__init__.py`: 导出 Typer `app`。
- `src/muxdev/cli/app.py`: 兼容性 app 导出。
- `pyproject.toml`: 配置 `muxdev = "muxdev.cli:app"`。

## CLI 主模块

`src/muxdev/cli/main.py` 是命令注册中心。当前命令大致分为：

- daemon: `start`, `serve`, `dashboard`
- task: `design`, `dev`, `fix`, `refactor`, `review`, `test`, `ci`, `why`
- lifecycle: `tasks`, `status`, `continue`, `stop`, `retry`, `skip`, `merge`, `report`, `diff`, `rollback`
- approvals: `approvals`, `approve`, `deny`
- provider actions: `actions`, `action handled`, `action dismiss`
- memory governance: `memory status/query/propose/approve/quarantine/contradictions/quarantine-auto`
- parallel/learning/multirepo: `parallel conflicts`, `learning provider`, `multirepo plan/design/dev`
- ecosystem automation: `feedback add/list`, `cache list`, `skill catalog/lock/verify`, plugin manifest commands
- local tools: `provider`, `config`, `preset`, `policy`, `trace`, `metrics`, `search`, `mcp`, `session`, `rag`, `graph`, `deep-agent`, `workflow`, `flow`, `skill`, `plugin`
- UI: `repl`, `tui`

旧顶层别名 `run/resume/web/account/install/doctor` 不再作为主路径；provider 相关能力集中在 `muxdev provider ...`。

## CLI 辅助模块

### `cli/common.py`

跨命令复用的 helper：

- JSON 输出。
- daemon client 构造。
- provider account/install 输出。
- CSV/role provider 解析。
- service started 面板。

业务逻辑不放这里，应该放到 `config/`、`providers/`、`services/`、`daemon/`。

### `cli/providers.py`

注册 provider 子命令：

- `provider detect`
- `provider list`
- `provider doctor`
- `provider account`
- `provider install`

实际探测来自 `providers/registry.py`，账号/安装文档来自 `config/accounts.py` 和 `config/installers.py`。

### `cli/tui.py`

daemon-backed TUI 交互循环。职责：

- 解析 slash command。
- 调用 `DaemonClient`。
- 处理 provider-actions API 缺失的旧 daemon 提示。
- 使用 `ui/tui.py` 渲染 Rich 文本。
- 在当前任务存在 provider action 时展示 prompt、options、attach command。

TUI 不调用 provider detect，也不向 provider CLI stdin 写入 yes/no。

## 任务提交路径

以：

```powershell
muxdev dev "add tests" --provider mock --json
```

为例：

1. Typer 调用 `cli/main.py::dev`。
2. `dev` 调用 `_submit_main_task(...)`。
3. `_submit_main_task` 调用 `config/runtime.py::resolve_task_request`。
4. runtime config 合并 builtin/global/project/task/CLI options。
5. 解析 profile、gate、roles、provider、workflow、automation flags。
6. 调用 `services/skills/activation.py::resolve_active_skills`，兼容导入仍通过 `services/skill_engine.py` 暴露。
7. 生成 daemon task payload。
8. `clients/daemon.py::DaemonClient.submit_task` 发送 `POST /api/tasks`。
9. API 调用 `TaskManager.submit_task`。
10. daemon 创建 run，并启动 worker thread。

关键文件：

- `cli/main.py`
- `config/runtime.py`
- `services/automation.py`
- `services/skill_engine.py`
- `services/skills/`
- `clients/daemon.py`
- `api/web.py`
- `daemon/tasks.py`

## API 层

`api/web.py::create_app` 创建 FastAPI app，并可注入 `TaskManager` 方便测试。

核心路由：

- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/continue`
- `POST /api/tasks/{task_id}/stop`
- `GET /api/tasks/{task_id}/diff`
- `GET /api/tasks/{task_id}/report`
- `POST /api/tasks/{task_id}/rollback`
- `GET /api/tasks/{task_id}/attach-command`

审批和 provider action：

- `GET /api/approvals`
- `POST /api/approvals/{approval_id}/approve`
- `POST /api/approvals/{approval_id}/deny`
- `GET /api/provider-actions`
- `GET /api/tasks/{task_id}/provider-actions`
- `POST /api/provider-actions/{action_id}/handled`
- `POST /api/provider-actions/{action_id}/dismiss`

生态、学习与并行 API：

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

Dashboard HTML 也在 `api/web.py` 中渲染。

## Daemon TaskManager

`daemon/tasks.py::TaskManager` 是 daemon 状态写入边界。

重要方法：

- `submit_task`: 创建 run、写 `task_context.json`、启动 worker。
- `continue_task`: 检查 pending provider actions/approvals 后 resume。
- `stop_task`: 标记任务 aborted。
- `list_tasks`: 构建任务列表。
- `task_detail`: 构建 Dashboard/TUI detail payload。
- `approvals`: 列出 muxdev approvals。
- `decide_approval`: approve/deny。
- `provider_actions`: 列出 provider actions。
- `decide_provider_action`: handled/dismiss。
- `provider_scores`: provider score 可见性。
- `parallel_conflicts`: parallel-squad 冲突可见性。
- `semantic_merge_reviews`: semantic merge 可见性。
- `provider_learning`: provider learning snapshots。
- `ecosystem_state`: feedback/cache/skill/plugin/guardrail 生态状态。
- `diff/report/rollback/attach_command`: 任务操作。
- `subscribe/broadcast`: WebSocket event queue。

runtime 构造：

```python
SupervisorRuntime(
    workspace,
    runs_dir=paths.runs_dir,
    state_db=paths.db_path,
    worktrees_root=paths.worktrees_dir,
    write_dashboards=False,
)
```

## Runtime

`runtime/supervisor.py::SupervisorRuntime` 是实际执行器。

执行流程：

1. 创建 run 目录和 isolated worktree。
2. 加载 workflow DAG。
3. 写 `task.md`、`workflow.yaml`、`task_context.json`。
4. 创建 agent/stage/role contract。
5. 读取项目 memory context。
6. 调用 provider adapter。
7. 解析 stream events 和 provider actions。
8. 处理 provider fallback、read-only gate、budget gate。
9. 应用 safety policy 和 muxdev approvals。
10. 写 role result、evidence、hash ledger。
11. 写 provider attempts 和 session capsules。
12. 运行 semantic merge reviewer。
13. 运行 Blind Validator Panel。
14. 写 final report、diff、stage snapshot、rollback metadata。
15. 更新 provider score 和 provider learning。

当 provider CLI 需要人工确认时，runtime 写入 `provider_actions`，run/stage 进入 `awaiting_provider_action`。`continue` 在 action pending 时不会重新启动 worker。

当 muxdev gate 需要审批时，runtime 写入 `approvals`，run/stage 进入 `awaiting_approval`。

## Provider 与 Stream

Provider 层：

- `providers/registry.py`: provider 定义、静态探测、capability state。
- `providers/adapters.py`: runtime adapter。
- `providers/mock.py`: deterministic mock provider。
- `clients/stream.py`: provider output 解析和 ProviderActionRequest 生成。

`clients/stream.py` 会识别：

- `[y/N]`
- `Approve?`
- 登录/鉴权提示
- 限流提示
- attach/session 相关提示
- idle/provider blocked 模式

无法解析明确选项时，也会保留原始 prompt 片段、transcript/chunks 路径和 attach command。

## Service Capability Map

### 自动、设计与记忆

- `services/automation.py`: intent resolver、flow selector、role topology。
- `services/design.py`: design brief、design review、design artifact。
- `storage/memory.py`: memory proposal/query/approval 基础能力。

### 可信交付

- `services/evidence.py`: Evidence v2 events、manifest、evaluation、verification 和 legacy cleanup。
- `storage/ledger.py`: hash ledger。
- `storage/contracts.py`: role contract。

### 运行时安全与 Provider 稳定性

- `services/session_capsules.py`: session capsule 和 handoff patch。
- `services/provider_scores.py`: provider attempt/score 汇总。
- `clients/stream.py`: provider action 解析。

### 生态自动化与技能治理

- `services/feedback.py`: feedback router 和 CI rescue。
- `services/cas_cache.py`: CAS cache。
- `services/skills/`: skill discovery、catalog、trust、activation、selection、lock、eval。
- `services/skill_engine.py`: backward-compatible facade。
- `services/skill_lock.py`: backward-compatible lock facade。
- `api/mcp.py`: MCP guardrail tools。

### 并行、语义合并、学习与多仓

- `services/advanced_parallel.py`: parallel-squad conflict detection。
- `services/semantic_merge.py`: semantic merge reviewer。
- `services/provider_learning.py`: cross-run provider learning。
- `services/multirepo.py`: multi-repo orchestration planning。
- `storage/memory.py`: contradiction detection 和 quarantine automation。

## Storage

### Blackboard

`storage/blackboard.py` 封装 SQLite schema 和读写方法。daemon 模式使用：

```text
~/.muxdev/data/muxdev.sqlite
```

本地 ecosystem 命令使用：

```text
<repo>/.muxdev/ecosystem.sqlite
```

### Memory

`storage/memory.py` 负责：

- memory proposal
- approval/quarantine
- query
- contradiction detection
- automatic quarantine

项目 DB：

```text
<repo>/.muxdev/memory.sqlite
```

### Trace

`storage/trace.py` 提供：

- trace JSONL 读取
- compact trace
- Chrome trace 导出

trace 文件在：

```text
~/.muxdev/data/runs/<run_id>/trace.jsonl
```

## Config

`config/runtime.py` 是 TOML-first 配置主实现。

核心函数：

- `load_runtime_config`
- `resolve_task_request`
- `setup_muxdev`
- `set_runtime_config_value`
- `config_check`

合并顺序：

```text
builtin < global < project < task < CLI options
```

`config/loader.py` 仍用于历史 YAML provider/workflow/path 兼容。

## Skill Governance

`services/skills/` 是当前 skill governance 实现包，`services/skill_engine.py` 保留为兼容 facade。

核心能力：

- 扫描 `SKILL.md`
- 解析 frontmatter
- 读取 `skills.toml` 和 `muxdev.skill.toml`
- progressive catalog
- role binding
- trust/disable/auto policy
- task explicit activation
- metadata auto-match
- provider injection mode 标记
- whole-tree skill lock
- skill eval/score/abtest

激活顺序：

```text
task explicit -s
  -> role binding in skills.toml
  -> metadata auto-match
```

## UI

### Rich TUI

`ui/tui.py` 只负责渲染：

- daemon chat view
- tasks
- task detail
- approvals
- provider actions
- report
- diff
- parallel conflicts
- provider learning

交互循环在 `cli/tui.py`。

### Dashboard

`api/web.py::render_live_dashboard_html` 通过 JS 请求 `/api`：

- tasks
- approvals
- provider actions
- ecosystem
- provider scores
- learning
- parallel conflicts
- semantic merge reviews
- multi-repo orchestrations

### REPL

`ui/repl.py` 保留轻量本地 REPL，不替代 daemon TUI。

## MCP

`api/mcp.py` 提供最小 JSON-RPC surface。当前工具包括 provider detect、workspace search、RAG query、workflow templates、flow render、guardrail-safe blackboard read 等。MCP 是本地工具能力，不直接接管 daemon task lifecycle。

## 测试定位

按能力找测试可用 `pytest -k`：

- CLI 与别名清理: `tests/test_cli.py`
- daemon/API/client: `tests/test_daemon_client_server.py`
- TUI: `tests/test_repl_tui_m7.py`
- automation/memory: `python -m pytest -q -k "auto_request or design_runtime"`
- trusted delivery: `python -m pytest -q -k "trusted_delivery or approval_subject_drift"`
- provider actions/runtime safety: `python -m pytest -q -k "read_only_stage or provider_action_writes"`
- ecosystem automation: `python -m pytest -q -k "feedback_router or skill_lock or mcp_guardrail"`
- parallel/learning/multirepo: `python -m pytest -q -k "parallel_conflict or provider_learning or multi_repo"`
- product experience: `python -m pytest -q -k "product_experience or project_setup"`
- Evidence v2: `python -m pytest -q -k "evidence_v2"`
- Skill Governance v2: `python -m pytest -q -k "skill"`
- runtime/storage: `tests/test_runtime_m1_m4.py`
- stream/workflow/safety: `tests/test_stream_workflow_safety.py`
- import structure: `tests/test_structure.py`

推荐回归：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pytest -q
```

## 修改定位建议

- 新 CLI 参数或命令：`cli/main.py` 或对应 CLI 子模块。
- TUI 命令或文案：`cli/tui.py` + `ui/tui.py`。
- Dashboard/API：`api/web.py`。
- task lifecycle：`daemon/tasks.py`。
- runtime stage 行为：`runtime/supervisor.py`。
- provider 探测：`providers/registry.py`。
- provider 执行：`providers/adapters.py`。
- provider action 解析：`clients/stream.py`。
- TOML runtime config：`config/runtime.py`。
- skill 扫描/激活：`services/skills/`，兼容入口在 `services/skill_engine.py`。
- evidence/validator：`services/evidence.py`。
- provider scores/learning：`services/provider_scores.py`、`services/provider_learning.py`。
- feedback/cache：`services/feedback.py`、`services/cas_cache.py`。
- parallel/semantic merge/multirepo：`services/advanced_parallel.py`、`services/semantic_merge.py`、`services/multirepo.py`。
- SQLite schema 或 run 状态：`storage/blackboard.py`。
- memory：`storage/memory.py`。
- trace 展示：`storage/trace.py`。
