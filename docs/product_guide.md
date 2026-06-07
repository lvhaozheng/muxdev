# muxdev Product Guide

## Positioning

muxdev 是一个本地优先的 AI 软件研发控制内核，面向多 AI Coding Agent 时代。

一句话定位：

```text
muxdev 让多个 AI Coding Agent 在本地变成一个可信的软件交付系统。
```

它不是“第 N 个多 Agent 终端管理器”，也不是普通 AI coding CLI。muxdev 负责把 Codex、Claude Code、Qwen、Kimi、Gemini-style CLI、OpenCode-style CLI、Trae、Cursor-style 工具和内置 `mock` provider 纳入统一的本地 SDLC 控制面，让 AI 交付结果可验证、可审批、可回滚、可审计、可恢复、可治理、可持续学习。

底层 Agent 负责执行，muxdev 负责调度、策略、记忆、审批、证据、回滚和治理。

## Why muxdev exists

AI coding 工具正在快速增多。不同工具各自有入口、配置、权限、日志、上下文和失败模式。真实工程任务往往也不是“让一个 Agent 写完代码”这么简单，而是需要规划、编码、测试、安全审查、文档和 CI 修复共同完成。

团队落地时会遇到几个共同问题：

- 多个 provider CLI 分散，难以统一分配角色和跟踪运行状态。
- Agent 的自然语言结论难以直接作为可信交付依据。
- 审批如果只是一句“同意”，无法绑定具体 plan、patch、policy 或 evidence。
- provider 卡死、认证失败、限流、跑偏、重复 token 消耗时缺少恢复机制。
- 项目经验很难可靠沉淀，错误记忆和过期架构可能被反复复用。

muxdev 的价值是把零散 AI coding 工具统一放进本地 SDLC 控制内核中：任务从设计、开发、修复、测试、审查，到 CI、回滚、报告和知识沉淀，都由 muxdev 统一编排和治理。

## What muxdev solves

### 多 Agent 工具碎片化

muxdev 通过 provider capability matrix 和 role-aware routing 统一管理 provider。不同 provider 可以被分配到不同角色，例如：

```text
plan   -> claude-code
code   -> codex
test   -> qwen
review -> codex
secure -> claude-code
docs   -> mock 或其他 provider
```

用户也可以用 `--role role=provider` 覆盖默认选择。

### AI 交付不可验证

muxdev 不直接相信任何 Agent 的自然语言结论，而是相信结构化 contract、可复现 evidence、独立 validator 和绑定 hash 的审批对象。

可信交付链路包括：

- StageContract
- RoleResultContract
- EvidenceBundle
- Hash-Chained Ledger
- Blind Validator Panel
- Semantic Merge Review
- Approval Subject Hash
- Stage Snapshot Rollback

### 长期记忆污染

muxdev Memory 不是聊天记录缓存。它是带证据来源、作用范围、角色适用性、可信度、生命周期和审批状态的工程知识。

只有被 final report、human approval、CI evidence、安全审查或明确 artifact 支撑的结论，才应该成为长期记忆。

### 人工审批粗糙

muxdev 的审批绑定具体对象，而不是绑定一句自然语言确认。

例如：

```text
approval_subject = hash(plan_hash + patch_hash + policy_hash)
```

plan 改了，plan approval 失效；patch 改了，merge approval 失效；policy 改了，相关 approval 失效。

### 失败不可恢复

muxdev 通过 isolated run worktree、stage snapshot、rollback、event trace、session capsule handoff 和 provider action，把失败恢复变成一等能力。

当 provider 认证失败、等待 CLI 确认、限流、卡死或跑偏时，muxdev 不把它伪装成普通 approval，而是记录 Provider Action，并要求用户进入对应 CLI/session 处理后再继续。

## Core Design Principles

### 入口简单，复杂度内置

用户不需要先理解复杂 DAG、多 Agent 拓扑或 YAML 配置。日常入口应该足够简单：

```powershell
muxdev design "设计持久化记忆系统"
muxdev dev "增加 Redis 限流"
muxdev fix "修复登录测试"
muxdev refactor "拆分 billing 模块"
muxdev review
muxdev test
muxdev ci fix
muxdev why latest
muxdev report latest
```

复杂度由系统内置：muxdev 自动判断任务风险、流程深度、是否需要 review、secure、evidence、approval 和 memory 注入。

### 自动决策，而不是手动堆配置

muxdev 通过这些能力决定任务该怎么跑：

- Intent Resolver
- Repo + Risk + Memory Analyzer
- Auto Flow Selector
- Role Topology Compiler

流程深度：

```text
simple / safe / deep / parallel / ci
```

角色拓扑：

```text
solo / pair / squad / parallel-squad / ci
```

简单任务折叠角色，复杂任务展开角色，高风险任务强制激活 test、review、secure 等验证角色。

### 多 Agent 保留，但由系统自动编排

muxdev 保留并强化 `plan/code/test/review/secure/docs` 等角色，同时扩展 `architect/requirements/test_strategy/memory_curator` 等设计和治理角色。

显式覆盖示例：

```powershell
muxdev dev "支付风控" --deep

muxdev dev "高风险支付变更" `
  --role plan=codex `
  --role code=qwen `
  --role review=claude-code `
  --role secure=claude-code
```

### 设计成为一等公民

`muxdev design` 与 `dev/fix/review/test` 同级。它默认不写实现代码，而是输出 Design Pack 和 Memory Proposals。

典型内容：

- problem statement
- requirements
- architecture options
- decision record
- system design
- API/data model
- risk/threat model
- test strategy
- implementation roadmap
- final design review
- memory proposals

这让 muxdev 支持“先设计、再实现、再验证、再沉淀”的工程链路。

## Core Capabilities

### Auto Flow Selector

根据任务类型、代码影响面、敏感路径、测试标记、历史失败、provider 状态和项目记忆，自动选择流程深度。

用户不用理解内部 DAG，muxdev 自动判断是否需要 plan、review、secure、多 Agent 并行、evidence report 或人工 approval。

### Role Topology Compiler

根据任务复杂度自动展开角色。

小修复可以只用一个 code agent；涉及认证、支付、权限、迁移、依赖升级等高风险任务时，muxdev 会自动启用 plan、code、test、review、secure 等角色，并在必要时要求审批。

### Evidence-Grounded Persistent Memory

Memory 存储项目架构决策、代码约定、测试命令、模块边界、安全规则、provider 表现、skill 成功率等内容。

常用命令：

```powershell
muxdev memory status
muxdev memory query "当前项目的认证边界是什么？"
muxdev memory propose latest
muxdev memory approve <mem_id>
muxdev memory quarantine <mem_id>
muxdev memory contradictions --json
muxdev memory quarantine-auto --json
```

### Trusted Delivery Evidence

每次 AI 修改都应该回答清楚：

- 需求是什么？
- 计划是什么？
- 谁实现的？
- 谁验证的？
- 测试结果是什么？
- patch hash 是什么？
- 审批绑定了什么对象？
- 是否可以回滚？

muxdev 通过 contracts、evidence bundle、ledger、validator 和 report 记录这些信息。

### Approval Integrity

审批绑定具体对象：

- `plan_hash`
- `patch_hash`
- `policy_hash`
- `validator_hash`
- `semantic_review_hash`
- `memory_hash`

任何关键对象变化都应该让旧审批失效。

### Rollback、Sentinel、Handoff

muxdev 支持：

- isolated run worktree
- stage snapshot rollback
- trace/report/diff
- session capsule handoff
- provider action handoff
- provider fallback
- budget gate
- read-only write violation detection

### Policy Engine

最小权限角色策略限制每个 role 的读写、shell、network、merge 权限。

默认倾向：

- plan/review/secure 只读
- code 需要 write gate
- shell 需要 shell gate
- merge 需要 merge gate
- provider CLI 的外部确认走 Provider Action

### Provider Conformance 与 Adaptive Role Router

muxdev 不只是“支持很多 provider”，还会持续记录不同 provider 在不同 role 上的表现：

- 成功率
- 失败率
- 人工介入率
- provider action 次数
- 成本
- 延迟
- fallback 情况

这些信息会进入 provider score 和 provider learning，用于后续自动选择。

### Feedback Router 与 CI Rescue

外部反馈统一抽象为事件：

- local test failure
- CI log
- GitHub PR comment
- review comment
- issue comment
- manual feedback
- security blocker

反馈会被路由给 test、code、secure 或 plan 等角色，形成本地测试、CI、PR review 和 issue 反馈的闭环修复。

### CAS Cache、Skill Lock 与 MCP Guardrail

muxdev 的生态治理能力包括：

- CAS Cache：缓存反馈、测试、构建、RAG、provider conformance 等可复用信息。
- Skill Lock：记录 skill hash、version、compatible roles 和 failure modes。
- Safe Plugin Manifest：校验插件权限，不默认执行插件代码。
- MCP Guardrail：让外部 MCP 工具复用 muxdev 的审批、记忆、证据链和安全策略。

## Typical Workflows

### 个人开发者

```powershell
muxdev dev "增加 Redis 限流"
muxdev status latest
muxdev diff latest
muxdev report latest
```

muxdev 自动判断是否需要 plan、test、review、secure、approval，并生成 diff、trace、report 和 evidence。

### 团队研发流程

团队可以把 roles、gate、policy、memory、skills 固化到仓库配置中，让每个人使用同一套 AI 研发规范。

```toml
[automation]
mode = "auto"
profile = "auto"
depth = "auto"

[roles]
plan = "auto"
code = "auto"
test = "auto"
review = "auto"
secure = "auto"

[memory]
enabled = true
mode = "evidence-grounded"
local_only = true
```

### 高风险代码库

认证、支付、权限、风控、迁移、依赖升级等高风险改动可以自动启用 strict gate、secure role、blind validator、approval hash 和 rollback snapshot。

```powershell
muxdev dev "重构支付权限边界" --deep -g strict
```

### CI 与自动化修复

```powershell
muxdev feedback add ci_failed "pytest failed in tests/test_login.py" --source github-actions --provider mock --json
muxdev ci rescue "npm test failed on auth flow" --source github-actions --provider mock --json
```

当超过重试阈值或需要人工判断时，任务进入 human gate 或 provider action，而不是无限循环。

## Complex Operations

### Approvals

Approvals 是 muxdev 的策略审批，例如 plan、write、shell、merge。

```powershell
muxdev approvals --status pending --json
muxdev approve <approval_id>
muxdev deny <approval_id>
muxdev continue latest
```

### Provider Actions

Provider Actions 是外部 provider CLI/session 阻塞，例如 CLI confirmation、auth required、rate limit、idle timeout。

```powershell
muxdev actions --status pending --json
muxdev attach <run_id> --agent code
muxdev action handled <action_id>
muxdev action dismiss <action_id>
muxdev continue <run_id>
```

Dashboard/TUI 不会替用户向 provider CLI 输入 yes/no。用户先进入对应 CLI/session 处理，再回到 muxdev 标记 handled。

### Evidence Verify

```powershell
muxdev evidence verify latest --json
```

用于校验 ledger、contracts、evidence bundle 和 validator panel。

### Rollback

```powershell
muxdev rollback latest --to-stage code --json
```

rollback 只处理 isolated run worktree，不直接破坏当前工作区。

### Parallel 与 Semantic Merge

```powershell
muxdev refactor "拆分 billing 模块" --parallel
muxdev parallel conflicts --status open --json
```

parallel-squad 需要 conflict-aware planned writes 和 semantic merge review 才能进入可信交付路径。

### Multi-Repo Planning

```powershell
muxdev multirepo plan "coordinate auth API change" --repo repo-a --repo repo-b --mode design --json
muxdev multirepo dev "coordinate auth API change" --repo repo-a --repo repo-b --json
```

P4 v1 生成可审计编排计划，不自动跨仓写代码。

## How muxdev differs from other AI coding tools

muxdev 不直接替代 Aider、Cline、OpenHands、Continue、Codex CLI、Gemini CLI、Qwen Code 或 Claude Code。

这些工具大多关注：

- 如何让 AI 更好地写代码。
- 如何让 Agent 在 IDE、Terminal、PR 中工作。
- 如何执行代码修改、测试、审查或自动化任务。

muxdev 关注的是更上层的问题：

- 如何管理多个 AI Coding Agent。
- 如何根据任务自动选择流程深度。
- 如何让不同 provider 负责不同角色。
- 如何让 AI 交付结果可验证。
- 如何让审批绑定 plan、patch、policy。
- 如何让失败可回滚、可恢复。
- 如何让项目经验变成可信记忆。
- 如何让本地 AI 软件研发流程可治理。

因此，muxdev 更像是 AI Coding Agent 之上的本地控制平面 / SDLC Kernel。

## Framework Backends: MetaGPT, CrewAI, AutoGen

MetaGPT、CrewAI 和 AutoGen 适合作为 muxdev 的可选执行或编排后端，而不是替代 muxdev 控制面。

建议分工：

- MetaGPT：SOP、设计、PRD、架构、任务拆解。
- CrewAI：role crew execution，适合 sequential/hierarchical 的稳定角色协作。
- AutoGen：advanced team/graph backend，适合复杂讨论、GraphFlow 实验和并行分析。

保持不变的 muxdev 核心：

- daemon
- WorkflowDefinition canonical schema
- Blackboard
- approvals
- provider actions
- evidence
- rollback
- memory
- Dashboard/TUI/API

推荐策略：

```text
native 控制面 + 可选 framework backend
```

也就是说，外部 framework 可以增强某个 stage 或 role 的执行质量，但所有输出仍必须回到 muxdev 的 artifact、provider_attempt、evidence、ledger 和 report。

## Roadmap Direction

muxdev 的最终方向不是单纯让 Agent 跑起来，而是让多个 Agent 跑出来的结果具备可信交付能力。

收敛目标：

- 入口简单：`design/dev/fix/review/test/ci`
- 自动决策：`simple/safe/deep/parallel/ci`
- 角色完整：`plan/code/test/review/secure/docs/architect/memory_curator`
- 多 Agent 编排：不同 provider 按 role 自动分配
- 本地优先：状态、证据、审批、记忆默认保存在本地
- 记忆可信：只沉淀有 evidence、approval 或 CI 支撑的工程知识
- 结果可验证：contracts、evidence、validator、ledger
- 审批可追踪：approval 绑定 plan/patch/policy/memory hash
- 失败可恢复：rollback、event replay、session capsule handoff
- 安全可治理：least privilege role、policy engine、safe plugin、MCP guardrail
- 生态可扩展：provider conformance、feedback router、framework backend、Dashboard/TUI
