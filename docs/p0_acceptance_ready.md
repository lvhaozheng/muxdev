# P0 Acceptance Ready Guide: Auto + Role + Design + Memory

本指南用于验收 muxdev P0 能力：把自动决策、角色拓扑、设计流和可信记忆骨架打通，并确认后续 P1 的证据链/验证闭环可以接上。

## 验收目标

P0 的 ready 标准不是“所有治理能力完成”，而是以下主链路已经可运行、可解释、可落盘、可继续扩展：

- 用户入口保持简单：`muxdev design/dev/fix/refactor/review/test/ci fix`。
- 默认自动：命令会编译出 intent、flow depth、role topology、roles。
- 用户可覆盖：`-p solo|pair|squad|ci`、`--role role=provider`、`--simple|--safe|--deep|--parallel`。
- `muxdev design` 作为一等命令，只产出 Design Pack，不写实现代码。
- Design Pack 包含 `design_contract.json` 和 `memory_proposals.json`。
- 项目 memory 使用 `.muxdev/memory.sqlite`，包含 `memory_items` 与 `memory_evidence` 基础 schema。
- `muxdev memory propose/approve/query/status` 可完成 propose -> approve -> retrieve 的最小闭环。
- `muxdev why` 可解释本次 intent/depth/topology/roles 选择。

## 快速验收

建议在干净测试目录中执行，provider 使用内置 `mock`：

```powershell
python -m pip install -e ".[test]"
muxdev setup --project --yes
muxdev start
```

### 1. 自动决策与角色拓扑

```powershell
muxdev dev "harden auth login boundary" --provider mock --gate auto --json
muxdev why latest
```

通过标准：

- JSON payload 或 `muxdev why latest` 中能看到 `automation.intent = dev`。
- `depth` 对敏感 auth/login 任务应为 `deep`。
- `topology` 应为 `squad`。
- roles 至少包含 `plan/code/test/review`。
- run 的 `task_context.json` 中包含 `automation`。

### 2. 显式覆盖

```powershell
muxdev dev "small copy fix" --provider mock --simple --json
muxdev dev "parallel module migration" --provider mock --parallel --json
muxdev dev "ship feature" --provider mock -p pair --role code=mock --json
```

通过标准：

- `--simple` 让 `depth = simple`。
- `--parallel` 让 `topology = parallel-squad`。
- `-p pair` 覆盖自动 profile。
- `--role code=mock` 同时映射 runtime role 和 legacy role provider。

### 3. Design Pack

```powershell
muxdev design "design evidence-grounded memory for muxdev" --provider mock --json
muxdev status latest --json
```

通过标准：

- workflow 为 `design`。
- run artifacts 中包含 `design_contract.json` 和 `memory_proposals.json`。
- run 目录下存在：

```text
design/
  00_problem_statement.md
  01_requirements.md
  02_architecture_options.md
  03_decision_record.md
  04_system_design.md
  05_api_and_data_model.md
  06_risk_and_threat_model.md
  07_test_strategy.md
  08_implementation_roadmap.md
  09_open_questions.md
  10_final_design_review.md
  design_contract.json
  memory_proposals.json
```

### 4. 从设计进入开发

如果 design run 使用 workspace 本地 `.muxdev/runs`，可执行：

```powershell
muxdev dev --from-design latest --provider mock --gate auto --json
```

通过标准：

- dev task 文本中绑定 design contract 摘要。
- 后续 run 的 `task_context.json` 仍包含新的 automation 决策。

### 5. Memory 最小闭环

```powershell
muxdev memory status
muxdev memory query "" --status proposed --json
muxdev memory approve <mem_id>
muxdev memory query "memory" --json
```

通过标准：

- `.muxdev/memory.sqlite` 存在。
- `memory_items` 中 proposed item 可被 approve 为 active。
- `memory_evidence` 记录 design contract 或 final report 的路径与 sha256。
- active memory 会在后续自动决策中作为 `automation.memory_context` / `memory_refs` 绑定。

## 自动决策验收矩阵

| 场景 | 命令 | 期望 intent | 期望 depth | 期望 topology |
| --- | --- | --- | --- | --- |
| 普通开发 | `muxdev dev "add cache"` | `dev` | `safe` | `squad` |
| 敏感路径 | `muxdev dev "change payment auth"` | `dev` | `deep` | `squad` |
| 小修复 | `muxdev fix "fix typo"` | `fix` | `simple` | `solo` |
| 设计任务 | `muxdev design "design memory"` | `design` | `deep` | `squad` |
| 重构 | `muxdev refactor "split billing"` | `refactor` | `deep` | `squad` |
| CI 修复 | `muxdev ci fix` | `ci` | `ci` | `ci` |
| 并行覆盖 | `muxdev dev "migrate modules" --parallel` | `dev` | `parallel` | `parallel-squad` |

## 自动化测试

推荐在代码验收时运行：

```powershell
python -B -m pytest tests\test_strategy_main_path.py tests\test_cli.py tests\test_config_loader.py tests\test_p0_automation_memory.py -q
```

通过标准：

- P0 测试全部通过。
- 主路径 CLI/config/daemon 测试无回归。
- 如果 Windows 环境无法写 `.pytest_cache` 或 `__pycache__`，使用 `python -B`；这不影响功能验收。

## 已知边界

- P0 memory retrieval 是轻量关键词匹配，不是向量检索或冲突检测。
- `memory approve` 是本地命令审批，P1 需要把 approval 绑定到 hash。
- `design` 现在生成 Design Pack 骨架和 provider stage 输出，P1 可升级为结构化 DesignContract 校验。
- Blind Validator、Hash Ledger、Rollback stage snapshot、Provider score 已在 P1/P2 落地；P0 验收只确认这些后续能力能从自动决策、角色拓扑、设计产物和 memory context 继续接上。

## Ready 判定

当以下命令都能稳定执行并产生可检查 artifact 时，P0 可以判定为 ready：

```powershell
muxdev dev "harden auth login boundary" --provider mock --gate auto --json
muxdev why latest
muxdev design "design persistent memory" --provider mock --json
muxdev memory status
muxdev memory query "" --status proposed --json
muxdev memory approve <mem_id>
muxdev memory query "persistent memory" --json
python -B -m pytest tests\test_p0_automation_memory.py -q
```
