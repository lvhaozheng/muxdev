# muxdev 中文参考

muxdev 的产品定义是“AI Agent 可信交付控制面”，不是通用多 Agent 平台。

## 执行模型

四个工作流为 `change / design / review / test`。Profile 只改变必需阶段、Evidence Requirement、独立/安全评审、人工确认、签名、超时、重试和成本上限；不会动态生成工作流。

一次运行由 `TaskService` 管理生命周期，`RunEngine` 持久化 Job、Stage 和 typed append-only event。固定 DAG 按确定性 Frontier 执行；严格变更工作流会把普通/安全 Review 作为同一 Subject 上的只读 Worker 并行执行并按固定顺序归并。已完成阶段不会在恢复时重复执行；无法证明安全重放的外部 Provider 阶段会被阻断并要求协调。每个 Run 最多使用两次安全恢复动作，下一轮模型会收到上一轮的契约错误、失败检查、Review blocker 或 Provider 终止信息；仅输出契约错误时优先保留代码并执行只读 JSON 纠正。

Conversation 默认使用固定角色团队：规划、实现、测试、评审，Strict 增加安全评审。`provider` 表示主要实现 Agent，`role_providers` 可覆盖当前 Workflow 使用的角色；每个 Stage/Attempt 都有独立 Worker 身份，只有可写实现角色允许跨修订续接。Standard/Strict 的 Reviewer 必须与实现 Provider 不同。系统不会动态生成子任务或并行写入：写阶段始终串行，同一冻结 Subject 上的只读阶段最多 4 个并行。

阶段 Agent 可以把不明确需求作为结构化选项题发送到 Conversation，并允许自由输入。低风险问题在 60 秒未回复时采用明确标出的推荐项并重跑当前阶段；权限、删除、凭据、网络、门禁和交付接受等高风险问题必须显式确认。Conversation API 同时返回 `interactions`、基于完成阶段的 `progress` 和逐阶段 `stage_deliveries`。Dashboard 右侧把标准、产物、Artifact、Evidence 分开显示；修订阶段标准会创建新 Contract、标记受影响的下游 Stage，绝不修改原 Run 的冻结策略或 Evidence。

Provider 失败会区分超时、临时网络/限流、认证、权限/沙箱、命令配置、进程异常和未知退出。只有临时类与未知退出会安全重试；只有冻结策略中存在合格备用 Provider 时才返回 `switch-provider`。Conversation API 的 `team` 与 `recovery` 投影、SSE Worker 事件和 Dashboard 失败卡会显示 Agent、阶段、退出码、脱敏详情、尝试历史、剩余额度、工作区状态和服务端允许的下一步。后台异常不会再停留在不明状态，而会写入 `run.failed_to_start` 或 `recovery.failed` 并回到 `needs_user`。

每个 Stage 接收统一 `StageExecutionInput`。Provider Codec 负责不同 CLI 的 Prompt 传输和 JSONL 输出协议。下游 Stage 通过预算化 Context Pack 获取前序结构化事实、Repo Map 和 Verified PASS 历史；检索 Memory 不具备 Policy 权限。

## Evidence 与门禁

EvidencePolicy 是唯一规则源，五种记录为 Artifact、Check、Review、Interaction 和 Runtime。TestResult 中的 argv 仅是 `VerificationSuggestion`，Runtime 只执行 Workflow/项目策略拥有且已冻结的 argv 数组，并通过 `ExecutedCheck` 生成 CheckEvidence；Provider 的 `passed`、exit code、confidence、Evidence 或 delivery decision 均不能决定 Gate。

Gate 的每个失败项都包含稳定 `requirement_id`、原因、Evidence 引用和修复建议。四维 Scorecard 展示分子、分母和失败 Requirement；分母为零显示 N/A。分数只解释质量，因此高分运行仍可能因缺少批准而 BLOCKED。

运行只产生 `evidence-report.json`。`muxdev evidence export <run-id>` 可额外生成 DSSE Envelope，绑定报告摘要但不复制业务事实。

## 常用命令

```powershell
muxdev init
muxdev run "task" --workflow change --profile lite --provider mock
muxdev show <run-id>
muxdev resume <run-id> --action auto  # 也可用 fix-output / retry / switch-provider
muxdev approve <interaction-id>
muxdev evidence show <run-id>
muxdev evidence verify <run-id>
muxdev route explain <run-id>
muxdev skill verify default-test
muxdev provider certify codex-acp --live
muxdev mcp serve --transport stdio
muxdev migrate status
```

每个 Run 会冻结 Workflow、Provider fingerprint、Skill 锁、CapabilityGrant、MCP 工具和工作区 Manifest。项目级 Skill 只能请求 Grant 子集，不能扩大权限或改变 EvidencePolicy。MCP 只负责向外暴露控制面、向内投影当前 Stage 的精确只读工具；普通工具调用仍由 Coding CLI/ACP Agent 原生完成。ACP 每个 Stage 使用一个本地 Session，权限越界立即拒绝。旧 `[delivery_gate]` 和 `## Delivery Standard` 仅产生迁移警告；生产门禁必须迁移到 `evidence-policy.yaml`。

## 教学与面试材料

- [项目教学与面试答辩规范](../../release-artifacts/muxdev-teaching-playbook-cn.md)：导师授课规则、10 单元课程、真实难点矩阵、评分与毕业门禁。
- [130 个面试问题族](../../release-artifacts/muxdev-interview-drill-bank-cn.md)：覆盖产品、架构、Provider、DAG、Evidence、安全、Memory、恢复、路由、测试、选型、行为面试与登录场景。
- [Q1-Q22 深度答辩](../../release-artifacts/interview-qa-cn.md)：零基础长答案和代码示例。
