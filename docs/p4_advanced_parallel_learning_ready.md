# P4 Advanced Parallel And Long-Term Learning Acceptance Ready

本文用于验收 muxdev P4 能力：在 P0-P3 的自动化、可信交付、运行时安全和生态自动化之上，打通高级并行与长期学习闭环。

## 能力摘要

- Conflict-aware parallel-squad 会记录并阻断高风险并行写冲突。
- Semantic Merge Reviewer 会在最终 merge gate 前独立审查 diff，并把结果写入 `semantic_merge_reviews`。
- Cross-run Provider Learning 会把 provider attempts/actions 聚合成持久化学习快照。
- Memory Contradiction Detection 会识别明显相互否定的 project memory。
- Memory Quarantine Automation 会自动隔离低置信度或未生效的矛盾记忆。
- Multi-repo design/dev orchestration 会生成跨仓编排计划和每个仓库的执行命令。
- Dashboard/TUI 会展示 P4 状态；TUI 中 `/dev`、`/design` 仍走自动路由，并新增 `/parallel`、`/learning` 只读入口。

## 1. 基础准备

```powershell
muxdev setup --project --yes
muxdev start
```

若 TUI 报旧 daemon 404，先重启：

```powershell
muxdev serve --restart
muxdev tui
```

## 2. 验收并行冲突感知

准备一个写入计划文件：

```json
{
  "code_a": ["src/auth.py", "docs/plan.md"],
  "code_b": ["src/auth.py"],
  "docs": ["docs/plan.md"]
}
```

执行：

```powershell
muxdev parallel conflicts --file writes.json --json
```

预期：

- `src/auth.py` 产生 `high` 冲突。
- `docs/plan.md` 产生 `medium` 冲突。
- 若加 `--record`，冲突写入本地 `.muxdev/ecosystem.sqlite`。

## 3. 验收 Semantic Merge Reviewer

普通 run 完成后检查：

```powershell
muxdev dev "semantic merge smoke" --provider mock --gate auto --json
muxdev status latest --json
muxdev evidence verify latest --json
```

预期：

- run 目录存在 `validation/semantic_merge_review.json`。
- Dashboard 的 `Advanced Parallel -> Semantic Merge` 可见审查结果。
- 若 diff 中出现 `<<<<<<<`、`=======`、`>>>>>>>`，run 会被 `semantic_merge_reject` 阻断。

## 4. 验收 Provider Learning

执行：

```powershell
muxdev learning provider --json
muxdev provider score --json
```

预期：

- `learning provider` 返回持久化学习快照。
- 字段包含 `provider/role/attempts/successes/failures/human_actions/score`。
- provider action 或失败越多，分数会下降；成功 attempt 会提高对应 role 的学习快照。

## 5. 验收 Memory 矛盾检测与自动隔离

```powershell
muxdev memory contradictions --json
muxdev memory quarantine-auto --json
muxdev memory query "" --status quarantined --json
```

预期：

- 明显相互否定的记忆会进入 `memory_contradictions`。
- `quarantine-auto` 会隔离低置信度或 proposed 侧记忆。
- quarantined memory 不会进入后续自动 memory context。

## 6. 验收 Multi-Repo 编排

```powershell
muxdev multirepo plan "coordinate auth API change" --repo path\to\repo-a --repo path\to\repo-b --mode design --json
muxdev multirepo dev "coordinate auth API change" --repo path\to\repo-a --repo path\to\repo-b --json
```

预期：

- 生成 `.muxdev/multi-repo/<orchestration_id>.json`。
- 每个 repo 都有 `workflow`、`command`、`markers`、`status`。
- v1 只生成计划，不自动跨仓提交修改。

## 7. Dashboard / TUI 验收

```powershell
muxdev dashboard
muxdev tui
```

TUI 可用命令：

```text
/dev <task>
/design <task>
/parallel
/learning
/actions
/status latest
```

预期：

- `/dev`、`/design` 仍使用 Auto Flow Selector 和 Role Topology Compiler。
- Current Task 面板显示 `parallel conflicts`、`semantic merge`、`provider learning`。
- `/parallel` 展示 open parallel-squad conflicts。
- `/learning` 展示 provider learning snapshots。
- Dashboard 详情页展示 `Parallel Conflicts`、`Semantic Merge Reviews`、`Provider Learning`、`Multi-Repo Orchestration`。

## 8. API 验收

```powershell
curl http://127.0.0.1:8788/api/parallel-conflicts
curl http://127.0.0.1:8788/api/semantic-merge-reviews
curl http://127.0.0.1:8788/api/learning/provider
curl http://127.0.0.1:8788/api/multi-repo/orchestrations
curl http://127.0.0.1:8788/api/memory/contradictions
```

任务级 API：

```powershell
curl http://127.0.0.1:8788/api/tasks/<run_id>/parallel-conflicts
curl http://127.0.0.1:8788/api/tasks/<run_id>/semantic-merge-reviews
```

## 9. 回归检查

```powershell
python -m pytest tests/test_p4_advanced_parallel_learning.py -q
python -m pytest -q
```

预期：

- P4 专项测试全部通过。
- P0-P3 现有 daemon/client/server/runtime/dashboard/TUI 测试继续通过。

## 常见问题

- `muxdev tui` 提示某个 API 404：正在运行的是旧 daemon，执行 `muxdev serve --restart`。
- `parallel conflicts` 没有输出：没有提供 planned write hints，或当前没有记录 open conflicts。
- `learning provider` 为空：还没有 provider attempts/actions，可先执行一次 mock run 或 provider action 场景。
- `memory contradictions` 为空：当前 memory claims 没有明显相互否定的重叠事实。
- `multirepo plan` 不会执行代码修改：P4 v1 只生成可审计编排计划，后续再由用户进入每个 repo 执行对应 `muxdev design/dev`。
