# muxdev 架构文档

本文描述当前 `muxdev` 的实现架构。当前主线是本地 Client-Server 控制面：CLI/TUI/Dashboard 作为客户端，daemon 作为唯一任务生命周期控制者，运行时复用 `SupervisorRuntime` 执行 workflow、provider、安全策略和 artifact 生成。

## 目标

muxdev 的目标是把本地 AI coding 工作流收敛到一个可观测、可审批、可恢复的控制面：

- 用 `muxdev start` / `muxdev serve` 启动本地 daemon。
- 用 `muxdev dev/fix/review/test` 提交任务。
- daemon 统一管理任务状态、审批、报告、diff、rollback、attach command、事件流和 Dashboard。
- provider/config/skill/RAG/flow/MCP 等工具命令保留为本地 CLI 能力。

旧的顶层兼容命令和 Python shim 已被移除。公开命令以 `dev/fix/review/test`、`continue`、`dashboard`、`provider ...` 为准。

## 运行视图

```text
User
  |
  | muxdev / muxdev tui / muxdev dev
  v
CLI and TUI clients
  |
  | HTTP via DaemonClient
  v
FastAPI daemon
  |
  | TaskManager owns state and workers
  v
SupervisorRuntime
  |
  | workflow stages, provider adapters, safety gates
  v
Global state store + run artifacts
```

默认端口：

- Dashboard: `http://127.0.0.1:8787`
- API: `http://127.0.0.1:8788`

两个端口运行同一套 FastAPI 应用和 `/api` 合约。Dashboard 使用相对 `/api` 请求，CLI 默认通过 `DaemonClient` 访问 API 端口。

## 目录结构

当前源码主层级如下：

```text
src/muxdev/
  api/        FastAPI app、MCP JSON-RPC、Dashboard HTML
  cli/        Typer 命令入口、TUI 客户端、provider 子命令
  clients/    daemon HTTP client、session/stream client
  config/     TOML 主配置、默认 YAML、provider account/install 配置
  core/       平台适配、脱敏、安全策略
  daemon/     daemon 路径、进程管理、server、TaskManager
  models/     领域模型和状态枚举
  providers/  provider 探测、registry、runtime adapters
  runtime/    SupervisorRuntime 和 worktree 管理
  services/   dashboard、flows、RAG、reports、skills、orchestration helpers
  storage/    SQLite blackboard 和 JSONL trace
  ui/         Rich 渲染、REPL、TUI 视图
  workflows/  workflow parser 和 DAG 工具
```

`reports/`、`sessions/`、`stream/`、`workflow/`、`orchestration/`、`safety/` 旧源码入口已清理或迁移。若本地还能看到这些目录，通常是权限锁住的 `__pycache__` 残留，不应再放入源码。

## 组件职责

### CLI

入口为 `muxdev.cli:app`，由 `pyproject.toml` 暴露为 `muxdev` 命令。

主要模块：

- `cli/main.py`: Typer 根命令和大多数命令注册。
- `cli/common.py`: JSON 输出、daemon client 创建、provider account/install 输出辅助。
- `cli/providers.py`: `muxdev provider detect/list/doctor/account/install`。
- `cli/tui.py`: daemon-backed 对话式 TUI 客户端循环。
- `cli/app.py`: 只保留 `app` 兼容导出，不承载命令实现。

任务类命令不直接写 SQLite，也不直接运行 `SupervisorRuntime`。它们解析配置、profile、gate、roles、skills 后，通过 `DaemonClient` 提交到 daemon。

### Daemon

daemon 相关模块位于 `daemon/`：

- `paths.py`: `MUXDEV_HOME`、数据目录、PID、日志、DB、runs/worktrees 路径。
- `process.py`: 后台启动、停止、状态检查。Windows 下通过隐藏 subprocess 启动，避免频繁弹 terminal。
- `server.py`: uvicorn server 入口，同时启动 API 和 UI 端口。
- `tasks.py`: `TaskManager`，负责任务生命周期、状态库写入、worker 线程、事件广播。

daemon 首次启动会确保：

```text
~/.muxdev/
  config.toml
  data/
    muxdev.sqlite
    logs/daemon.log
    runs/<run_id>/
    worktrees/
```

### API 和 Dashboard

`api/web.py` 创建 FastAPI app，并提供：

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
- `GET /api/approvals`
- `POST /api/approvals/{approval_id}/approve`
- `POST /api/approvals/{approval_id}/deny`
- `WS /events`
- `WS /api/events`

同一个模块还渲染 live dashboard HTML。历史 run-level 静态 dashboard 的渲染函数仍作为 service 能力保留，但公开入口已经转向 daemon Dashboard。

### TaskManager

`TaskManager` 是 daemon 侧任务控制核心：

- 创建 task id / run id。
- 创建 daemon run artifact 目录。
- 在全局 SQLite 中创建 run。
- 启动后台 worker 线程运行 `SupervisorRuntime`。
- 处理 `continue/stop/rollback/approve/deny/attach`。
- 汇总 task list/detail payload。
- 维护 WebSocket subscriber queue。

TaskManager 使用 daemon-owned `Blackboard`：

```python
Blackboard(paths.data_dir, db_path=paths.db_path)
```

这保证只有 daemon runtime 写全局 `muxdev.sqlite`。

### Runtime

`runtime/supervisor.py` 中的 `SupervisorRuntime` 负责实际 workflow 执行：

- 加载 workflow DAG。
- 准备 worktree。
- 选择 provider adapter。
- 执行 stage。
- 应用 safety policy、approval gates、budget gates。
- 写 trace、blackboard、artifacts、report、diff。
- 支持 resume/retry。

daemon 注入：

- `runs_dir=~/.muxdev/data/runs`
- `state_db=~/.muxdev/data/muxdev.sqlite`
- `worktrees_root=~/.muxdev/data/worktrees`
- `write_dashboards=False`

这使 runtime 行为复用原有逻辑，但状态归 daemon 管。

### Storage

`storage/blackboard.py` 是 SQLite state store。核心语义包括：

- `runs`
- `stages`
- `agents`
- `approvals`
- `artifacts`
- `test_results`
- `review_blockers`
- `usage_records`
- `checkpoints`
- `error_details`

`storage/trace.py` 负责 JSONL trace 读写和压缩展示。trace 文件仍放在每个 run artifact 目录中：

```text
~/.muxdev/data/runs/<run_id>/trace.jsonl
```

### Config

当前配置主路径是 TOML：

- 全局: `~/.muxdev/config.toml`
- 项目: `<repo>/.muxdev/config.toml`
- 任务文件: `-f task.toml` 或 `MUXDEV_TASK_CONFIG`

合并顺序：

```text
builtin < global < project < task < CLI options
```

旧 YAML loader 仍用于默认 provider/workflow/path 等低层配置兼容，但新命令默认写 TOML。

### Skill Engine

`services/skill_engine.py` 负责：

- 扫描 `SKILL.md`
- 解析 frontmatter
- 处理 `skills.toml`
- role binding
- disable/enable/trust/auto policy
- task explicit activation
- metadata auto-match
- provider injection mode 标记

扫描优先级为：

```text
configured dirs > project dirs > global dirs > builtin
```

同名 skill 由高优先级覆盖低优先级。

## 数据流

### 提交任务

```text
muxdev dev "task"
  -> cli/main.py resolves config/profile/gate/roles/skills
  -> DaemonClient.post /api/tasks
  -> FastAPI create_task
  -> TaskManager.submit_task
  -> Blackboard.create_run
  -> background Thread
  -> SupervisorRuntime.run
  -> stages/provider/safety/storage/artifacts
  -> TaskManager.broadcast task_updated
```

### 审批

```text
runtime hits approval gate
  -> Blackboard.approvals pending
  -> task status awaiting_approval
  -> Dashboard/TUI/CLI list approvals
  -> muxdev approve <id>
  -> POST /api/approvals/<id>/approve
  -> Blackboard.decide_approval
  -> muxdev continue latest
  -> SupervisorRuntime.resume
```

### TUI

```text
muxdev or muxdev tui
  -> cli/tui.py
  -> DaemonClient.health/tasks/task/approvals
  -> ui/tui.py Rich render functions
  -> slash commands call daemon API
```

TUI 渲染路径不做 provider 实时探测，避免 Windows 环境下触发外部 CLI 导致 terminal 反复弹出。

## 进程模型

`muxdev start` 和 `muxdev serve --daemon` 通过 `daemon/process.py` 启动后台服务。Windows 下使用隐藏窗口 subprocess 参数；POSIX 下使用普通后台 subprocess。

PID 文件用于：

- `muxdev serve --status`
- `muxdev serve --stop`
- `muxdev serve --restart`

当前没有 systemd/launchd installer，后续可扩展为服务注册器。

## 错误和边界

- CLI task 命令不直接写数据库。
- daemon 是全局状态库唯一写入方。
- provider discovery 是静态探测，不执行模型调用。
- local daemon HTTP client 使用 `trust_env=False`，避免 localhost 请求被代理转发导致 502。
- `ci` gate 下需要人工审批时，runtime 可进入 blocked，而不是等待交互。
- Windows 运行外部命令时使用 `hidden_subprocess_kwargs()` 降低弹窗风险。

## 扩展点

当前已经有以下扩展入口：

- provider registry 和 adapters
- workflow YAML / built-in workflow aliases
- skill engine 和 `skills.toml`
- MCP JSON-RPC tools
- flow registry
- plugin registry stub
- Dashboard/TUI 对 daemon API 的客户端化渲染

新增能力应优先挂在 `services/`、`providers/`、`workflows/` 或 `daemon/`，避免恢复旧的根级 shim。
