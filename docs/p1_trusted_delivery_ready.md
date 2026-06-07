# P1 Trusted Delivery Ready Guide: 构建可信交付闭环

本文用于验收 muxdev P1 能力：在 P0 的自动化、角色、设计和记忆骨架之上，打通可信交付闭环，让每一次运行都具备可追踪、可校验、可审批、防漂移、可回滚的最小闭环。

## 验收目标

P1 ready 的判定不是“所有治理能力都完美”，而是以下链路已经能端到端运行：

- 每个 stage 进入前写入 `StageContract`，记录任务、工作流、provider、前置 patch hash。
- 每个 agent stage 结束后写入 `EvidenceBundle` 和 `RoleResultContract`。
- 运行过程写入 hash-chained `ledger.jsonl`，黑板同步记录 ledger events。
- 最终交付前运行 Blind Validator Panel，并把 validator hash 纳入 merge approval subject。
- plan/write/shell/external/merge approval 都绑定 `subject_hash`，subject 漂移时必须重新审批。
- daemon rollback 支持 `--to-stage`，可回到指定 stage 执行前的 snapshot。
- `muxdev evidence verify` 能校验 ledger、contracts、evidence bundles、validator panels 的完整性。

## 新增证据目录

一次成功的 mock run 应至少包含：

```text
.muxdev/runs/<run_id>/
  ledger.jsonl
  contracts/
    <stage>.stage_contract.json
    <stage>.role_result.json
  evidence/
    <stage>.evidence.json
  snapshots/
    <stage>.patch
    <stage>.snapshot.json
  validation/
    blind_validator_panel.json
  blackboard.sqlite
  final_report.md
  diff.patch
```

黑板中应能看到这些 P1 表：

```text
stage_contracts
evidence_bundles
ledger_events
snapshots
validator_panels
approvals.subject_hash
approvals.subject_json
```

## 快速验收

建议在干净测试目录中使用内置 `mock` provider：

```powershell
python -m pip install -e ".[test]"
muxdev setup --project --yes
muxdev start
```

### 1. 跑通一次可信交付

```powershell
muxdev dev "trusted delivery smoke" --provider mock --gate auto --json
muxdev status latest --json
muxdev report latest
```

通过标准：

- run status 为 `completed`。
- `final_report.md` 包含 `Trusted Delivery Evidence`。
- `diff.patch` 存在。
- run 目录存在 `ledger.jsonl`、`contracts/`、`evidence/`、`snapshots/`、`validation/blind_validator_panel.json`。

### 2. 校验证据链

```powershell
muxdev evidence verify latest --json
```

通过标准：

- `valid = true`。
- `ledger.valid = true`。
- `contracts > 0`。
- `evidence_bundles > 0`。
- `validators = 1`。
- `errors = []`。

### 3. 检查审批完整性

使用 plan gate 暂停：

```powershell
muxdev dev "approval integrity smoke" --provider mock --require-approval plan --json
muxdev approvals --status pending --json
```

通过标准：

- pending approval 中包含 `subject_hash`。
- `subject_json` 中包含 `policy_hash` 和 plan 相关 hash。

漂移演练建议只在一次性测试 run 上做：

1. 修改该 run 的 `plan.md` 或对应 plan artifact。
2. 批准旧 approval。
3. 执行 `muxdev continue <run_id> --json`。

通过标准：

- run 重新进入 `awaiting_approval`。
- 新 pending approval 的 `subject_hash` 与旧值不同。
- `trace.jsonl` 中出现 `approval_subject_stale`。

### 4. 检查 Blind Validator

```powershell
muxdev status latest --json
```

也可以直接打开：

```text
.muxdev/runs/<run_id>/validation/blind_validator_panel.json
```

通过标准：

- `decision = accept`。
- `patch_hash` 与 `diff.patch` 的 sha256 对应。
- validator 只包含最小上下文：task hash、patch hash、test results、review blockers、errors。

如果 test/review 最新角色结果为 accept，历史上已经修复的 blocker 仍保留在黑板审计表中，但不会误伤最终 Blind Validator。

### 5. 按阶段 snapshot 回滚

daemon task 完成后执行：

```powershell
muxdev rollback latest --to-stage implement --json
```

通过标准：

- `status = rolled_back`。
- `to_stage = implement`。
- `snapshot` 指向 `snapshots/implement.patch`。
- 在临时无真实 Git HEAD 的 worktree 中，允许返回 `fallback`，但状态仍应为 `rolled_back`。

## 自动化验收

推荐运行：

```powershell
python -B -m pytest tests\test_p1_trusted_delivery.py -q
python -B -m pytest -q
```

通过标准：

- P1 专项测试全部通过。
- 全量测试通过。
- Windows 环境中 pytest cache 写入警告不影响功能验收。

## 故障定位

常见失败点：

- `muxdev evidence verify` 报 hash mismatch：对应 artifact 在生成后被修改，检查 `errors` 中的 path。
- ledger invalid：`ledger.jsonl` 事件顺序、`prev_hash` 或 `event_hash` 被破坏。
- approval 一直 pending：检查 `approvals.subject_json`，确认 subject hash 是否因为 plan、patch、validator 或 policy 改变。
- Blind Validator reject：检查 `validation/blind_validator_panel.json` 的 `findings`。
- `rollback --to-stage` failed：确认 `snapshots/<stage>.patch` 存在，并查看返回的 `stderr`。

## Ready 判定

当以下命令都能稳定通过时，P1 可以判定为 ready：

```powershell
muxdev dev "trusted delivery smoke" --provider mock --gate auto --json
muxdev evidence verify latest --json
muxdev rollback latest --to-stage implement --json
python -B -m pytest tests\test_p1_trusted_delivery.py -q
```

P1 ready 后，后续 P2 可以继续扩展 provider score、跨 run 质量趋势、远端签名、公证存储或更严格的 validator panel。
