# Advanced Parallel And Long-Term Learning Readiness

本指南用于验收 muxdev 的高级并行、语义合并、provider learning、记忆矛盾检测和多仓编排能力。

## 能力摘要

- Conflict-aware parallel-squad 记录并阻断高风险并行写冲突。
- Semantic Merge Reviewer 在最终 merge gate 前独立审查 diff。
- Cross-run Provider Learning 将 provider attempts/actions 聚合为持久学习快照。
- Memory Contradiction Detection 识别互相否定的 project memory。
- Memory Quarantine Automation 自动隔离低置信或未生效的矛盾记忆。
- Multi-repo orchestration 生成跨仓编排计划和每个仓库的执行命令。
- Dashboard/TUI 展示 parallel conflicts、semantic merge、provider learning 和 multi-repo 状态。

## 基础准备

```powershell
muxdev setup --project --yes
muxdev serve --restart
```

## 并行冲突感知

准备 planned writes 文件：

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
muxdev parallel conflicts --file writes.json --record --json
```

通过标准：

- `src/auth.py` 产生 high 冲突。
- `docs/plan.md` 产生 medium 冲突。
- `--record` 将冲突写入本地 ecosystem store。

## Semantic Merge Reviewer

```powershell
muxdev dev "semantic merge smoke" --provider mock --gate auto --json
muxdev status latest --json
muxdev evidence verify latest --json
```

通过标准：

- run 目录存在 `validation/semantic_merge_review.json`。
- Dashboard 可见 Semantic Merge Reviews。
- diff 中存在 unresolved conflict markers 时，run 会被 `semantic_merge_reject` 阻断。

## Provider Learning

```powershell
muxdev learning provider --json
muxdev learning provider --role code --json
muxdev provider score --json
```

通过标准：

- 返回 provider、role、attempts、successes、failures、human_actions、score。
- provider action 或失败 attempt 会降低相关 role 评分。
- 成功 attempt 会提高学习快照。

## Memory 矛盾检测与自动隔离

```powershell
muxdev memory contradictions --json
muxdev memory quarantine-auto --json
muxdev memory query "" --status quarantined --json
```

通过标准：

- 明显互相否定的记忆进入 `memory_contradictions`。
- `quarantine-auto` 隔离低置信或 proposed 侧记忆。
- quarantined memory 不再进入后续 provider context。

## Multi-Repo 编排

```powershell
muxdev multirepo plan "coordinate auth API change" --repo path\to\repo-a --repo path\to\repo-b --mode design --json
muxdev multirepo dev "coordinate auth API change" --repo path\to\repo-a --repo path\to\repo-b --json
```

通过标准：

- 生成 `.muxdev/multi-repo/<orchestration_id>.json`。
- 每个 repo 都有 workflow、command、markers、status。
- 当前实现只生成可审计计划，不自动跨仓提交修改。

## Dashboard、TUI 与 API

```powershell
muxdev dashboard
muxdev tui
```

TUI 常用只读入口：

```text
/parallel
/learning
```

API：

```powershell
curl http://127.0.0.1:8788/api/parallel-conflicts
curl http://127.0.0.1:8788/api/semantic-merge-reviews
curl http://127.0.0.1:8788/api/learning/provider
curl http://127.0.0.1:8788/api/multi-repo/orchestrations
curl http://127.0.0.1:8788/api/memory/contradictions
```

## 回归

```powershell
python -B -m pytest -q -k "parallel_conflict or semantic_merge or provider_learning or memory_contradiction or multi_repo"
python -B -m pytest -q
```
