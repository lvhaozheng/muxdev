# Resume bullets

## 中文

- 主导 AI Agent 交付控制面架构精简，将生产 Python 从 38,933 行降至 4,328 行（-88.9%）、文件从 166 降至 52（-68.7%），消除循环依赖及全部超 120 行函数。
- 设计 Evidence v3 确定性门禁：五类运行时证据、稳定 Requirement ID、Subject 摘要绑定、独立评审、四维可解释 Scorecard；保证高分不能覆盖缺失审批或失败检查。
- 将 54 表多投影存储重构为 12 表 append-only 事实模型，实现哈希事件链、可恢复阶段状态机及 v7 备份—临时导入—计数/摘要校验—原子切换迁移。
- 收敛 11 个工作流/10 个 Skill/159 个 CLI 命令/99 个 HTTP 路由为 4 个工作流、5 个 Skill、29/18/8 CLI/HTTP/MCP 固定表面，并保留 Beta 下界智能路由、异构 Reviewer 与 DSSE Attestation。

## English

- Led an AI Agent delivery-control-plane simplification, reducing production Python from 38,933 to 4,328 lines (-88.9%) and 166 to 52 files (-68.7%), while eliminating dependency cycles and every function over 120 lines.
- Designed deterministic Evidence v3 gates with five runtime evidence types, stable requirement IDs, subject-digest binding, independent review, and a four-dimension explainable Scorecard where quality scores cannot waive hard failures.
- Replaced 54-table projection-heavy persistence with a 12-table append-only fact model, hash-chained events, durable stage recovery, and an atomic backup/import/count-and-digest-verify/switch v7 migration.
- Consolidated 11 workflows, 10 Skills, 159 CLI commands, and 99 HTTP routes into four workflows, five Skills, and fixed 29/18/8 CLI/HTTP/MCP surfaces while retaining Beta-bound routing, heterogeneous reviewers, and DSSE attestation.
