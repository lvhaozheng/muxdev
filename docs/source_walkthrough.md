# muxdev 代码解读文档

本文按“用户命令如何落到代码”的顺序解读当前实现。它面向需要维护或扩展 muxdev 的开发者。

## 入口

### Python 包入口

- `src/muxdev/__main__.py`: 支持 `python -m muxdev`。
- `src/muxdev/cli/__init__.py`: 导出 Typer `app`。
- `pyproject.toml`: 配置 `muxdev = "muxdev.cli:app"`。

### CLI 主模块

`src/muxdev/cli/main.py` 是命令注册中心。当前命令大致分为：

- daemon 控制: `start`, `serve`, `dashboard`
- 主任务路径: `dev`, `fix`, `review`, `test`
- task 操作: `tasks`, `status`, `continue`, `stop`, `retry`, `skip`, `merge`, `report`, `diff`, `rollback`
- approvals: `approvals`, `approve`, `deny`
- config/preset: `config`, `preset`
- tools: `policy`, `trace`, `metrics`, `search`, `mcp`, `session`, `rag`, `graph`, `deep-agent`, `workflow`, `flow`, `skill`, `plugin`
- UI: `repl`, `tui`

旧顶层别名 `run/resume/web/account/install/doctor` 已删除。provider 相关入口在 `muxdev provider ...` 下。

## CLI 拆分

### `cli/common.py`

保存跨命令复用的薄 helper：

- `_print_json`
- `_daemon_client`
- `_print_service_started`
- `_account_command`
- `_install_provider_command`
- `_parse_csv`
- `_role_providers`

业务逻辑仍在 `config/`、`providers/`、`daemon/`、`services/` 中。

### `cli/providers.py`

注册 provider 子命令：

- `provider detect`
- `provider list`
- `provider doctor`
- `provider account`
- `provider install`

它只做参数解析和输出，provider 探测实现来自 `providers/registry.py`，account/install 数据来自 `config/accounts.py` 和 `config/installers.py`。

### `cli/tui.py`

实现 daemon-backed 对话式 TUI：

- 首次进入可清屏。
- 后续命令结果以内联方式追加。
- slash command 调用 daemon API。
- 渲染使用 `ui/tui.py` 中的 Rich 视图函数。

它不调用 `detect_providers()`，这是 Windows 下避免反复弹 terminal 的关键约束。

## 任务提交路径

以 `muxdev dev "add tests" --provider mock --json` 为例：

1. Typer 调用 `cli/main.py::dev`。
2. `dev` 调用 `_submit_main_task(...)`。
3. `_submit_main_task` 调用 `config/runtime.py::resolve_task_request`。
4. 配置解析产出：
   - `task`
   - `workspace`
   - `provider`
   - `workflow`
   - `profile`
   - `gate`
   - `require_approval`
   - `role_providers`
   - `skill_specs`
   - `ci_block_on_approval`
5. CLI 调用 `resolve_active_skills`，把显式 skill、绑定 skill、自动匹配 skill 转为 payload。
6. CLI 用 `DaemonClient.submit_task` 发送 `POST /api/tasks`。
7. daemon 返回 `task_id`，CLI 输出 JSON 或 Rich panel。

核心代码：

- `cli/main.py::_submit_main_task`
- `config/runtime.py::resolve_task_request`
- `services/skill_engine.py::resolve_active_skills`
- `clients/daemon.py::DaemonClient.submit_task`

## Daemon API 路径

`api/web.py::create_app` 创建 FastAPI app。这个函数接收可选 `TaskManager`，便于测试注入 mock manager。

关键 request model：

```python
class TaskCreateRequest(BaseModel):
    task: str
    workspace: str | None = None
    provider: str = "mock"
    workflow: str = "software-dev"
    profile: str | None = None
    gate: str | None = None
    require_approval: list[str] = Field(default_factory=list)
    max_cost_usd: float = 0.5
    role_providers: dict[str, str] = Field(default_factory=dict)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    ci_block_on_approval: bool = False
```

API handler 基本是薄转发：

```text
FastAPI route -> TaskManager method -> storage/runtime/service
```

## TaskManager

`daemon/tasks.py::TaskManager` 是 daemon 状态写入边界。

重要方法：

- `submit_task`: 创建 run，写 `task_context.json`，启动 worker。
- `continue_task`: resume 后台 worker。
- `stop_task`: 设置 run 为 aborted。
- `list_tasks`: 汇总 task 列表。
- `task_detail`: 构建 Dashboard payload。
- `approvals`: 列出审批。
- `decide_approval`: approve/deny。
- `diff/report/rollback/attach_command`: 任务操作。
- `subscribe/broadcast`: WebSocket event queue。

`TaskManager._runtime` 构造 daemon 模式 runtime：

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

主要职责：

- 创建 run 目录和 worktree。
- 加载 workflow。
- 创建或连接 Blackboard。
- 写 `task.md`、`workflow.yaml`、`task_context.json`。
- 为 workflow role 创建 agent 记录。
- 按 DAG 执行 stage。
- 调 provider adapter。
- 应用 `core/safety.py` 中的 `SafetyPolicyEngine`。
- 写 artifacts、test_results、review_blockers、usage、checkpoints、errors。
- 写 `final_report.md` 和 `diff.patch`。

当遇到人工 gate，runtime 把 approval 写入 SQLite 并暂停。`continue` 后会通过 `resume` 继续执行。

## Workflow

Workflow 解析位于 `workflows/`：

- `workflows/__init__.py`: 公开 API。
- `workflows/engine.py`: YAML 加载、DAG 校验、执行顺序、stage 条件。

CLI 中 `graph export` 和 `deep-agent plan` 使用 `services/orchestration.py` 把 workflow 转为外部系统可读结构。

## Provider

Provider 相关层级：

- `providers/registry.py`: provider 定义、静态探测、capability state。
- `providers/adapters.py`: runtime adapter。
- `providers/mock.py`: deterministic mock provider。
- `config/accounts.py`: account/login 文案。
- `config/installers.py`: dry-run install plan。

探测命令只做静态检查，不进行模型调用。

## Config

`config/runtime.py` 是 TOML 主配置实现。

核心函数：

- `load_runtime_config`: 合并 builtin/global/project/task config。
- `resolve_task_request`: 把 CLI/task file/config 合成 daemon task request。
- `setup_muxdev`: 写入推荐 config 并缓存 provider 探测结果。
- `set_runtime_config_value`: 支持 `muxdev config set`。
- `config_check`: 校验 TOML 主配置和旧 YAML 兼容配置。

旧 YAML loader 在 `config/loader.py`，仍用于默认 provider/workflow/path 数据。

## Skill Engine

`services/skill_engine.py` 是当前 skill 主实现。

关键数据类型：

- `SkillInfo`: 一个发现到的 `SKILL.md`。
- `ActivatedSkill`: 已激活 skill，包含 role、reason、injection mode。

激活路径：

```text
task explicit -s
  -> role binding in skills.toml
  -> metadata auto-match
```

provider injection mode 当前是描述性字段：

- `codex`, `claude-code`: `native_or_passthrough`
- `qwen`, `kimi`: `prompt`
- `mock`: `context`

## Storage

### SQLite Blackboard

`storage/blackboard.py` 封装 SQLite schema 和写入方法。daemon 模式使用全局 DB：

```text
~/.muxdev/data/muxdev.sqlite
```

run-local artifact 仍写在：

```text
~/.muxdev/data/runs/<run_id>/
```

### Trace

`storage/trace.py` 提供：

- trace JSONL 读取。
- compact trace 生成。
- 供 CLI `trace view/chrome`、Dashboard、TUI 使用。

## UI

### Rich TUI

`ui/tui.py` 只负责渲染：

- `daemon_chat_view`
- `daemon_tasks_text`
- `daemon_task_detail_text`
- `daemon_approvals_text`
- `daemon_report_text`
- `daemon_diff_text`

交互循环不在这里，而在 `cli/tui.py`。

### REPL

`ui/repl.py` 保留轻量本地 REPL。它主要是工具型交互，不替代 daemon TUI。

### Dashboard

`api/web.py::render_live_dashboard_html` 输出 live Dashboard。它通过 JS 请求 `/api/tasks`、`/api/tasks/{id}`、`/api/approvals`，并连接 WebSocket events。

## MCP

`api/mcp.py` 提供最小 JSON-RPC surface。当前工具包括 provider detect、workspace search、RAG query、workflow plugin、flow list/render 等。MCP 命令仍是本地工具型命令，不直接承担 task lifecycle。

## 测试布局

主要测试文件：

- `test_cli.py`: CLI 命令和旧 alias 负向测试。
- `test_daemon_client_server.py`: FastAPI/TaskManager/DaemonClient。
- `test_strategy_main_path.py`: P0/P1 主路径、config、skill、daemon request。
- `test_runtime_m1_m4.py`: SupervisorRuntime 和 storage。
- `test_stream_workflow_safety.py`: stream/session/workflow/safety。
- `test_structure.py`: canonical imports 和旧 shim 源码删除。

推荐运行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m pytest
```

`PYTHONDONTWRITEBYTECODE=1` 可避免在 Windows 权限较紧的工作区继续写 `__pycache__`。

## 修改代码时的定位建议

- 新 CLI 参数或命令: `cli/main.py` 或对应 CLI 子模块。
- provider 探测能力: `providers/registry.py`。
- provider 执行逻辑: `providers/adapters.py`。
- workflow 解析或 DAG: `workflows/engine.py`。
- task lifecycle API: `api/web.py` + `daemon/tasks.py`。
- runtime stage 行为: `runtime/supervisor.py`。
- SQLite schema 或 run 状态: `storage/blackboard.py`。
- trace 展示: `storage/trace.py` + `ui/tui.py`。
- config 主路径: `config/runtime.py`。
- skill 扫描/激活: `services/skill_engine.py`。
