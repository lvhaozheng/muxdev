# muxdev

muxdev 是一个本地优先的 AI Agent 可信交付控制面。它只解决一件事：让一次 Agent 变更能够恢复、验证、解释和审计。它复用 Codex、Claude Code、Qwen 等 Coding Agent 作为 Worker，本身负责 Provider 路由、固定 Supervisor DAG、Evidence 与确定性门禁。

系统固定为四类工作流 `change / design / review / test`、三档 Profile `lite / standard / strict`、五个内置 Skill，以及唯一的 EvidencePolicy 和 Gate Engine。Provider 输出被视为声明；Diff、Runtime 重放的检查结果、目标摘要、Reviewer 身份、人工交互和事件链才是可采信事实。

## 快速开始

需要 Python 3.11+。

```powershell
python -m pip install -e ".[test]"
muxdev init
muxdev run "add a deterministic marker" --workflow change --profile lite --provider mock
muxdev list
muxdev evidence verify <run-id>
```

`mock` 只验证控制面、恢复和门禁语义，不代表真实 Provider 质量。`standard` 和 `strict` 要求 Runtime 可确认的独立 Reviewer；`strict` 还要求人工确认，并执行条件安全评审。

## 核心边界

- `TaskService`：创建、查询、恢复、取消任务和处理交互。
- `ConversationService`：维护一个开发目标的长期会话、隔离工作树、契约修订、内部 Run 与不可变交付候选。
- `RunEngine`：执行固定阶段、持久化事件、调用 Provider、重放确定性检查、运行门禁和恢复任务。
- `ProviderAdapter.execute(StageExecutionInput)`：唯一 Provider 执行接口，不接受 Provider 自报 Evidence 或门禁结论。
- `Provider protocol codecs`：统一 Stage 语义输入，但显式处理 stdin/argv Prompt 传输和 Codex/Claude JSONL 差异。
- `ContextPack`：在 12,000 字符预算内组合前序结构化事实、AST Repo Map 和仍可验证为 PASS 的历史交付；Memory 只能影响 Prompt，不能改变 Gate。
- `EvidencePolicy`：唯一硬门禁规则源；项目可通过 `evidence-policy.yaml` 覆盖 Requirement 和 Profile 映射。
- `evidence-report.json`：每次运行唯一 Evidence 事实报告；显式导出时才生成 `attestation.dsse.json`。

Gate 结果固定为 `PASS / BLOCKED / WAITING_HUMAN`。Scorecard 只计算 completeness、reproducibility、integrity、independence 四个可解释维度，不能覆盖硬门禁。

严格 `change` 工作流在测试后把普通 Review 与 Security Review 作为同一 Subject 上的只读 Worker 并行执行，再按固定 Stage 顺序 Fan-in。写阶段不并发；只读 Worker 若修改 Worktree，Runtime 会记录失败并阻止回写。Change 的修复循环最多两轮。

## Conversation Web 工作台

Web 首页以 Conversation 为主实体：需求澄清、实现、自愈、验证、接受交付和后续修订都保留在同一条会话时间线中。一个 Conversation 可以包含多个 Run 和 Delivery Candidate，但同一时间只有一个活动候选；普通讨论不会使候选失效，新的代码修改或工作区摘要变化会强制重新验证。

每个 Conversation 默认组建固定角色团队：规划、实现、测试、评审，Strict 额外加入安全评审。`provider` 是主要实现 Agent；API 可通过 `role_providers` 显式覆盖 `plan/code/test/review/secure/architect/test_strategy` 中当前 Workflow 使用的角色，未覆盖角色由 Router 自动分配。Standard/Strict 的评审角色必须与实现 Provider 独立。每个 Stage/Attempt 使用独立 Worker 身份；只有可写实现角色可以跨修订续接原会话，其他角色不会共享实现会话。它仍是固定可信 DAG，而不是动态 Agent swarm：写阶段串行，只读且依赖就绪的阶段最多 4 个并行，并按固定顺序归并。

Conversation 复用长期隔离工作树，并在 Provider 明确声明续接能力时恢复原实施者会话（默认支持 Codex/Claude Code）；会话 ID 只注入可写实施阶段，不会进入独立 Reviewer。其他 Provider 自动回退到同一工作树和受限消息上下文，不会伪造“已续接”状态。

Conversation Run 即使 Gate PASS 也不会立即写回用户仓库。Runtime 先冻结 Candidate、ChangeSet、契约版本、消息截止序号和 Evidence；用户选择“接受可信交付”时再次验证 Evidence 事件链与当前隔离工作区摘要，然后才冲突安全地应用 ChangeSet。后续修订会生成新的候选版本，之前已接受的 PASS 保持不可变。

Agent 在需求存在实质歧义时可以向 Conversation 发出结构化问题：2–4 个选项、一个推荐项，并允许开发者自行输入。低风险、非阻塞问题等待 60 秒后采用推荐项并从当前 Stage 重跑；权限扩大、删除/覆盖、凭据、网络、门禁变更和接受交付等高风险问题永不超时自动确认。Dashboard 用阶段完成度显示可解释进度，而不伪造模型内部百分比；右侧逐阶段区分交付标准、Agent 产物、Artifact 和 Evidence。人工修订阶段标准会创建新 Contract，并记录受影响的下游阶段；已冻结 Run 和既有 Evidence 不会被改写，执行期间完成的旧契约结果只保留审计、不能冒充新契约交付。

```powershell
# 本机访问
muxdev serve

# 经自管 HTTPS 反向代理或 VPN 远程访问
muxdev serve --host 0.0.0.0 --allow-remote --trusted-origin https://muxdev.example.com
```

非 loopback 绑定必须显式使用 `--allow-remote`。启动后终端会显示一次性设备配对码；远程浏览器使用独立、可撤销的安全会话。MuxDev 不提供云端中继，也不会将代码、日志或 Evidence 上传到外部服务。

## 失败诊断与安全自愈

每个 Run 共享最多两次恢复动作。Provider 非零退出会被分类为超时、临时网络/限流、认证、权限/沙箱、命令配置、进程异常或未知失败。只有超时、临时错误和未知退出会自动重试一次；只有冻结路由中存在合格备用 Provider 时才提供切换动作，认证、配置和权限错误不会盲目重试。Provider 超时、确定性检查失败或 Review blocker 会连同上一轮错误、脱敏日志、失败命令和输出摘要反馈给下一轮模型。若代码已经修改但结构化结果不符合契约，MuxDev 会先冻结隔离 Worktree，并用只读、无 Shell/网络/MCP 权限的纠错回合只修正 JSON；纠错越权写入会被检测并回滚。`standard/strict` 缺少独立 Reviewer 时会在执行前阻断，不消耗实现成本。

Dashboard、Conversation API 和 `evidence-report.json` 会区分首要原因与下游未执行项，并列出具体 Agent/阶段/退出码、脱敏详情、已尝试动作、剩余额度、工作区安全状态和真正可执行的下一步。Conversation SSE 会实时投影 `team.resolved`、Worker 生命周期和 fan-out 事件；后台启动或恢复异常也会回到稳定的 `needs_user`。相同恢复动作也可通过现有接口触发：

```powershell
muxdev resume <run-id> --action auto
muxdev resume <run-id> --action fix-output
muxdev resume <run-id> --action retry
muxdev resume <run-id> --action switch-provider
```

## Trusted Agent Harness v1

每次 Run 开始时，muxdev 会冻结 Workflow、Provider fingerprint、Skill 锁、可信验证命令、工作区 Manifest 与阶段 `CapabilityGrant`。恢复运行继续使用原快照，模型建议的命令和自报测试状态都不能成为 Gate 证据；只有 Runtime 实际执行的检查、Provider/ACP 事件和冲突安全的 ChangeSet 可以进入 EvidencePolicy。

MCP 与 ACP 保持可选且边界清晰：

- `pip install -e ".[interop]"` 才安装官方 MCP 与 ACP SDK，基础安装不导入它们。
- `muxdev mcp serve --transport stdio` 将现有 8 个控制面操作暴露给外部 MCP Client；业务仍只经过 `TaskService`。
- 内部不代理普通工具调用，只把当前 Stage 精确允许的只读 `server/tool` 配置投影给 Coding CLI 或 ACP Agent。
- ACP Provider 每个 Stage 启动一个本地 Session；权限请求与 CapabilityGrant 对照，越界立即拒绝，取消交给统一进程治理。
- `standard/strict` 只路由到 fingerprint 匹配的 live-certified Provider；二进制或启动配置改变会使认证失效。

```powershell
python -m pip install -e ".[interop]"
muxdev provider certify codex-acp --live
muxdev mcp serve --transport stdio
```

## 产品表面

- 30 个 CLI 叶子命令：运行、交互、Evidence、路由、Provider、Skill、配置、迁移、服务和 MCP stdio。
- 31 个 HTTP 接口：保留原有 Run 接口，并增加版本化 Conversation、Delivery、SSE 与设备配对接口。
- 8 个 MCP 工具：run/get/list/resume/cancel/respond/get_evidence/verify_evidence。
- 18 张 SQLite 表：原有 12 张 Run 事实表保持兼容，新增 6 张 Conversation、Contract、Candidate 与个人设备投影表；v7 数据仍执行备份、临时导入、校验和原子切换。

完整命令可运行 `muxdev --help`。设计与实测结果见：

- [精简报告](release-artifacts/simplification-report.md)
- [架构图](release-artifacts/architecture-diagrams.md)
- [开源架构调研与取舍](release-artifacts/open-source-research.md)
- [Evidence v3 对照样例](release-artifacts/evidence-v3-example.json)
- [登录接口模拟 Evidence Report](release-artifacts/login-evidence-example.json)
- [Q1-Q22 深度面试答辩](release-artifacts/interview-qa-cn.md)
- [muxdev 项目教学与面试答辩规范（导师版）](release-artifacts/muxdev-teaching-playbook-cn.md)
- [130 个面试问题族与回答要点](release-artifacts/muxdev-interview-drill-bank-cn.md)
- [AI Agent Infrastructure Case Study](release-artifacts/ai-agent-infrastructure-case-study.md)
- [面试讲述](release-artifacts/interview-narratives.md)
- [中英文简历 Bullet](release-artifacts/resume-bullets.md)
- [分层测试与耗时](release-artifacts/verification-report.md)
- [English reference](docs/en/README.md)

## 验证

```powershell
python -m ruff check src/muxdev scripts tests
python -m pytest -q
python scripts/architecture_metrics.py
python scripts/verify_teaching_pack.py
```

本项目借鉴 OpenHands typed append-only events、Codex submission/event/approval 分离、DBOS/LangGraph 的恢复与 fan-out 语义、SWE-agent 的 Agent/环境边界、Aider repo map 的预算化上下文，以及 in-toto 的 Subject/Predicate/Envelope 分层；只实现四个固定工作流所需的小型 Supervisor，不引入 LangGraph/Temporal 等第二套工作流运行时。
