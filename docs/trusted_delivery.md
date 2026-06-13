# Trusted Delivery Readiness

本指南用于验收 muxdev 的可信交付闭环：每次运行都应可追踪、可校验、可审批、防漂移，并可回滚。

## 验收目标

- 每个 stage 进入前写入 `StageContract`。
- 每个角色输出写入 role result contract。
- 运行过程写入 hash-chained `ledger.jsonl`，并同步到 blackboard。
- 最终交付前运行 Blind Validator Panel。
- plan/write/shell/external/merge approval 绑定 `subject_hash` 和 `subject_json`。
- `muxdev rollback --to-stage` 可以回到指定 stage 执行前的 snapshot。
- Evidence v2 生成事件流、manifest 和 gate-first evaluation。

## 运行证据

成功 run 至少应包含：

```text
.muxdev/runs/<run_id>/
  ledger.jsonl
  contracts/
    <stage>.stage_contract.json
    <stage>.role_result.json
  evidence/
    events.jsonl
    manifest.json
    evaluation.json
  snapshots/
    <stage>.patch
    <stage>.snapshot.json
  validation/
    blind_validator_panel.json
  blackboard.sqlite
  final_report.md
  diff.patch
```

blackboard 中应能看到：

```text
stage_contracts
evidence_events
evidence_manifests
evidence_evaluations
ledger_events
snapshots
validator_panels
approvals.subject_hash
approvals.subject_json
```

## 快速验收

```powershell
python -m pip install -e ".[test]"
muxdev setup --project --yes
muxdev serve --restart
```

### 跑通一次可信交付

```powershell
muxdev dev "trusted delivery smoke" --provider mock --gate auto --json
muxdev status latest --json
muxdev report latest
```

通过标准：

- run status 为 `completed`。
- `final_report.md` 包含 `Evidence Evaluation` 和 `Evidence v2 Integrity`。
- `diff.patch` 存在。
- run 目录存在 `ledger.jsonl`、`contracts/`、`evidence/`、`snapshots/`、`validation/blind_validator_panel.json`。

### 校验证据

```powershell
muxdev evidence latest
muxdev evidence latest --events
muxdev evidence verify latest --json
```

通过标准：

- `valid = true`。
- `events > 0`。
- `manifest.event_count` 与 events 行数一致。
- `head_hash` 与事件链最后一条 hash 一致。
- `errors = []`。

### 检查审批完整性

```powershell
muxdev dev "approval integrity smoke" --provider mock --require-approval plan --json
muxdev approvals --status pending --json
```

通过标准：

- pending approval 包含 `subject_hash`。
- `subject_json` 包含策略、计划或 patch 相关 hash。
- subject 漂移后应产生新的 pending approval。

### 按阶段回滚

```powershell
muxdev rollback latest --to-stage implement --json
```

通过标准：

- `status = rolled_back`。
- `to_stage = implement`。
- snapshot 指向对应 stage 的 patch。

## 回归

```powershell
python -B -m pytest -q -k "trusted_delivery or approval_subject_drift or evidence_verify or rollback_to_stage"
python -B -m pytest -q
```
