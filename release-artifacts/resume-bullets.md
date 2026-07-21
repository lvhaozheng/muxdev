# Resume bullets

## 中文

- 设计并实现面向多 Coding Agent 的本地可信 Harness：以不可变 RunPolicySnapshot、能力交集、真实 Provider fingerprint 认证、阶段级只读 MCP 配置投影、ACP 会话/权限适配、冲突安全 ChangeSet 与 Runtime Evidence 实现可替换 Agent 和最小权限交付。
- 主导 AI Agent 交付控制面架构精简，将生产 Python 从 38,933 行降至 7,042 行（-81.9%）、文件从 166 降至 63（-62.0%），消除循环依赖及全部超 120 行函数。
- 设计 Evidence v3 确定性门禁：五类运行时证据、稳定 Requirement ID、Subject 摘要绑定、独立/安全评审、四维可解释 Scorecard；保证高分不能覆盖缺失审批或失败检查。
- 将 54 表多投影存储重构为 12 表 append-only 事实模型，实现哈希事件链、可恢复阶段状态机及 v7 备份—临时导入—计数/摘要校验—原子切换迁移。
- 自研小型 Supervisor DAG，以确定性 Frontier 并发普通/安全只读审查，固定顺序 Fan-in 并检测越权写入；构建 12K 字符 Context Pack，仅以通过完整性复验的 PASS 交付做 BM25 Evidence-grounded RAG。
- 统一 Codex/Claude Code/Qwen 等 CLI 的 Stage 语义输入，通过独立协议 Codec 处理 stdin/argv Prompt 传输与 JSONL 输出差异，并保留 Beta 下界智能路由和异构 Reviewer。

## English

- Designed and implemented a local Trusted Harness for multiple coding agents using immutable run-policy snapshots, capability intersection, fingerprint-bound live Provider certification, Stage-scoped read-only MCP projection, ACP session/permission adaptation, conflict-safe ChangeSets, and runtime-derived evidence.
- Led an AI Agent delivery-control-plane simplification, reducing production Python from 38,933 to 7,042 lines (-81.9%) and 166 to 63 files (-62.0%), while eliminating dependency cycles and every function over 120 lines.
- Designed deterministic Evidence v3 gates with five runtime evidence types, stable requirement IDs, subject-digest binding, independent/security review, and a four-dimension explainable Scorecard where quality scores cannot waive hard failures.
- Replaced 54-table projection-heavy persistence with a 12-table append-only fact model, hash-chained events, durable stage recovery, and an atomic backup/import/count-and-digest-verify/switch v7 migration.
- Built a compact Supervisor DAG with deterministic frontiers, concurrent read-only ordinary/security review, ordered fan-in, and write-set detection; added 12K-character Context Packs with BM25 retrieval restricted to still-verifiable PASS deliveries.
- Unified semantic stage inputs across Codex, Claude Code, and Qwen while isolating stdin/argv prompt transport and JSONL decoding in provider protocol codecs; retained Beta-bound routing and heterogeneous reviewers.
