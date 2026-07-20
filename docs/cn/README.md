# muxdev 中文参考

muxdev 的产品定义是“AI Agent 可信交付控制面”，不是通用多 Agent 平台。

## 执行模型

四个工作流为 `change / design / review / test`。Profile 只改变必需阶段、Evidence Requirement、独立/安全评审、人工确认、签名、超时、重试和成本上限；不会动态生成工作流。

一次运行由 `TaskService` 管理生命周期，`RunEngine` 依次持久化 Job、Stage 和 typed append-only event。已完成阶段不会在恢复时重复执行；无法证明安全重放的外部 Provider 阶段会被阻断并要求协调。变更工作流最多执行两轮固定的 fix → test → review 修复循环。

## Evidence 与门禁

EvidencePolicy 是唯一规则源，五种记录为 Artifact、Check、Review、Interaction 和 Runtime。Runtime 会重放 TestResult 中的 argv，并以实际退出码、耗时、stdout/stderr 摘要和摘要值产生 CheckEvidence；Provider 的 `passed`、confidence、Evidence 或 delivery decision 均不能决定 Gate。

Gate 的每个失败项都包含稳定 `requirement_id`、原因、Evidence 引用和修复建议。四维 Scorecard 展示分子、分母和失败 Requirement；分母为零显示 N/A。分数只解释质量，因此高分运行仍可能因缺少批准而 BLOCKED。

运行只产生 `evidence-report.json`。`muxdev evidence export <run-id>` 可额外生成 DSSE Envelope，绑定报告摘要但不复制业务事实。

## 常用命令

```powershell
muxdev init
muxdev run "task" --workflow change --profile lite --provider mock
muxdev show <run-id>
muxdev resume <run-id>
muxdev approve <interaction-id>
muxdev evidence show <run-id>
muxdev evidence verify <run-id>
muxdev route explain <run-id>
muxdev skill verify default-test
muxdev migrate status
```

项目级 Skill 只能指导执行，不能扩大权限或改变 EvidencePolicy。旧 `[delivery_gate]` 和 `## Delivery Standard` 仅产生迁移警告；生产门禁必须迁移到 `evidence-policy.yaml`。
