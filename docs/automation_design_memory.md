# Automation, Design, And Memory Readiness

本指南用于验收 muxdev 的基础自动化能力：自动决策、角色拓扑、设计先行和显式项目记忆。

## 验收目标

- 用户入口保持简单：`muxdev design/dev/fix/refactor/review/test/ci fix`。
- 命令会根据任务、仓库信号和记忆上下文自动选择 intent、depth、topology 和 roles。
- 用户仍可显式覆盖：`-p solo|pair|squad|ci`、`--role role=provider`、`--simple|--safe|--deep|--parallel`。
- `muxdev design` 产出 Design Pack，不直接写实现代码。
- Design Pack 包含 `design_contract.json` 和 `memory_proposals.json`。
- 项目记忆使用 `.muxdev/memory.sqlite`，通过 propose -> approve -> retrieve 进入长期上下文。
- `muxdev why` 能解释本次自动决策。

## 快速验收

```powershell
python -m pip install -e ".[test]"
muxdev setup --project --yes
muxdev serve --restart
```

### 自动决策与角色拓扑

```powershell
muxdev dev "harden auth login boundary" --provider mock --gate auto --json
muxdev why latest
```

通过标准：

- `automation.intent = dev`。
- 敏感认证任务应选择更深的流程。
- topology 至少包含 plan、code、test、review 等关键角色。
- run 的 `task_context.json` 包含 `automation`。

### 显式覆盖

```powershell
muxdev dev "small copy fix" --provider mock --simple --json
muxdev dev "parallel module migration" --provider mock --parallel --json
muxdev dev "ship feature" --provider mock -p pair --role code=mock --json
```

通过标准：

- `--simple` 强制 simple depth。
- `--parallel` 选择 parallel-squad topology。
- `-p pair` 覆盖自动 profile。
- `--role code=mock` 同时影响 runtime role 和兼容路由。

### Design Pack

```powershell
muxdev design "design evidence-aware memory for muxdev" --provider mock --json
muxdev status latest --json
```

通过标准：

- workflow 为 `design`。
- run artifacts 包含 `design_contract.json` 与 `memory_proposals.json`。
- run 目录下存在 `design/00_problem_statement.md` 到 `design/10_final_design_review.md`。

### 从设计进入开发

```powershell
muxdev dev --from-design latest --provider mock --gate auto --json
```

通过标准：

- dev task 引用设计合同摘要。
- 新 run 的 `task_context.json` 仍包含本次自动化决策。

### 记忆最小闭环

```powershell
muxdev memory status
muxdev memory query "" --status proposed --json
muxdev memory approve <mem_id>
muxdev memory query "memory" --json
```

通过标准：

- `.muxdev/memory.sqlite` 存在。
- proposed item 可被 approve 为 active。
- active memory 会进入后续自动决策的 `memory_context` / `memory_refs`。

## 回归

```powershell
python -B -m pytest -q -k "auto_request or auto_design or cli_design or design_runtime"
python -B -m pytest -q
```

Windows 上如果 `.pytest_cache` 写入失败，通常只是权限 warning，不影响功能结果。
## 2026-06 Automation Model Update

- Automation now records `intent`, `depth`, `workflow`, `roles`, `reasons`,
  repository signals, and memory references.
- It no longer records `profile` or `topology` for new tasks. Legacy runs can
  still be read, but dashboard and CLI do not present `solo`, `pair`, `squad`,
  or `ci` as runtime choices.
- Model roles are capabilities used by LLM stages only:
  `requirements`, `plan`, `architect`, `code`, `test_strategy`, `test`,
  `review`, `secure`, `docs`, and `memory_curator`.
- `human_gate`, `delivery_gate`, and other system gates are stages, not roles.
  Dashboard renders them separately from model roles.
