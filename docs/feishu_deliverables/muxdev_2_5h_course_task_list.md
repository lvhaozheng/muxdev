# muxdev 2.5 小时课程任务清单

## 课程总览

- 总时长：145 分钟，控制在 150 分钟以内。
- 课程目标：让应届大学生能够围绕 muxdev 开源项目，讲清楚 AI Coding Agent 控制面的项目背景、竞品格局、核心价值、技术架构、设计思路、核心模块源码，以及它如何体现大模型应用开发岗位所需能力。
- 课程产出：源码阅读任务清单、课堂讲解提纲、面试表达点、1 分钟项目介绍、简历项目描述。

| 章节 | 主题 | 建议时长 | 能力关键词 |
| --- | --- | ---: | --- |
| 0 | 问题背景、竞品格局与核心价值 | 15 分钟 | 产品定位、竞品分析、Agent 控制面价值 |
| 1 | muxdev 整体架构与设计分层 | 20 分钟 | 架构分层、状态边界、本地优先 |
| 2 | Agent 编排核心：SupervisorRuntime 与 Workflow | 25 分钟 | 工作流编排、DAG、恢复、循环 |
| 3 | 大模型接入核心：Provider Adapter 与 Prompt Contract | 20 分钟 | 模型适配、Prompt Contract、流式解析 |
| 4 | 状态管理与可观测：TaskManager / Blackboard / Artifacts | 20 分钟 | 状态机、持久化、可观测、Artifact |
| 5 | 安全控制与可信交付：Approval / Provider Action / Evidence | 25 分钟 | 人类审批、Provider 等待、证据链 |
| 6 | 源码综合串讲与面试表达 | 20 分钟 | 源码表达、简历项目、面试复述 |

## 第 0 章：问题背景、竞品格局与核心价值

### 教学目标

- 能解释 AI Coding Agent 从“对话补全”走向“任务执行与交付控制”的背景。
- 能区分 IDE 助手、云端编码代理、CLI 编码代理和本地控制面的差异。
- 能说清 muxdev 的核心价值：把多个 provider CLI 变成可审计、可恢复、可治理的软件交付系统。
- 能把项目价值对应到大模型应用岗位中的产品理解、系统设计和风险控制能力。

### 源码 / 资料阅读任务

| 路径 / 资料 | 看什么 |
| --- | --- |
| `README.md` | 看项目一句话定位、Why muxdev、核心能力、Provider Actions 与 Approvals 的区别。 |
| `docs/product_guide.md` | 看 muxdev 面向用户解决什么问题，如何描述 local-first Agentic SDLC Kernel。 |
| `docs/architecture.md` | 看“不是 provider CLI 包装器，而是本地 daemon 驱动控制面”的架构表述。 |
| GitHub Copilot coding agent 官方资料 | 看云端代理如何定位为可分配任务的编码代理，用于竞品定位对照。 |
| Cursor、Claude Code、OpenAI Codex 官方资料 | 看 IDE / CLI / 云端编码代理的入口差异，不做功能清单背诵。 |

### 课堂讲解要点

- AI Coding 的问题从“能不能写代码”转向“能不能可靠交付”。
- Cursor 更偏 IDE 内体验，Claude Code / Codex 更偏 CLI 任务执行，Copilot coding agent 更偏云端任务代理。
- muxdev 的独特性是 local-first 控制面：状态、审批、证据、回滚、记忆都掌握在本地。
- 不要把 muxdev 讲成“套了一层 ChatGPT API”，它的核心在 orchestration 和 governance。
- 多 provider 的价值不是“模型越多越强”，而是让不同角色、不同风险、不同失败模式可以被统一治理。
- 应届生面试时重点讲“我读懂了一个工程化 Agent 系统如何把不稳定模型调用变成可信流程”。

### 面试表达点

- 我把 muxdev 理解为 AI Coding Agent 的本地控制面，重点不是生成代码，而是管理从任务到交付的完整生命周期。
- 它和普通 ChatGPT wrapper 的区别在于有 Runtime、Workflow、状态库、审批、Provider Action、Evidence 和可恢复机制。
- 通过竞品对比，我能说明它在本地可信交付和多 provider 治理上的定位。

## 第 1 章：muxdev 整体架构与设计分层

### 教学目标

- 能画出 CLI / TUI / Dashboard / API / daemon / runtime / provider / storage 的分层关系。
- 能说明哪些模块负责交互，哪些模块负责生命周期，哪些模块负责执行和持久化。
- 能理解“状态写入边界”对 Agent 系统可靠性的意义。
- 能用架构语言描述 local-first 系统如何兼顾自动化和人工可控。

### 源码 / 资料阅读任务

| 文件路径 | 看什么 |
| --- | --- |
| `docs/architecture.md` | 看顶层视图、目录结构、Runtime 分层、Approval 与 Provider Action 的架构解释。 |
| `docs/source_walkthrough.md` | 看源码导览如何把 CLI、API、TaskManager、Runtime、Provider、Storage 串起来。 |
| `src/muxdev/cli/main.py` | 只看命令注册和任务提交的职责边界，不把任务入口链路展开成单独章节。 |
| `src/muxdev/api/web.py` | 看 API 和 Dashboard 如何复用 daemon 状态，不看前端细节。 |
| `src/muxdev/daemon/tasks.py` | 看 `TaskManager` 为什么是 daemon 侧任务生命周期和状态写入边界。 |
| `src/muxdev/runtime/supervisor.py` | 看 `SupervisorRuntime` 作为执行核心如何被 daemon 注入和调用。 |

### 课堂讲解要点

- 顶层架构可以概括为：用户入口负责提交和观察，daemon 负责生命周期，runtime 负责编排执行。
- CLI / TUI / Dashboard 不直接接管 provider，它们通过 API 和 TaskManager 观察状态。
- TaskManager 是全局状态写入边界，避免多个入口各自改状态造成不可恢复。
- SupervisorRuntime 是执行层，管理 workflow、worktree、provider、审批、证据和报告。
- Blackboard / run artifacts 把过程状态从“控制台日志”变成可查询、可复盘的数据。
- local-first 的含义：代码、运行状态、证据、记忆、回滚信息优先保留在本机。
- 架构讲解不要罗列技术栈，要围绕“为什么这样分层能让 Agent 可靠交付”。

### 面试表达点

- 我能把 muxdev 拆成交互层、daemon 生命周期层、runtime 编排层、provider 执行层和 storage 事实层来讲。
- 它把状态写入集中到 TaskManager / Blackboard，降低多入口、多 provider 场景下的状态漂移风险。
- 这个架构体现了大模型应用开发中的系统边界设计和工程可观测能力。

## 第 2 章：Agent 编排核心：SupervisorRuntime 与 Workflow

### 教学目标

- 能说明 Workflow 如何用 stage、role、deps、gate、loop 表达 Agent 任务流程。
- 能理解 `SupervisorRuntime` 如何在一次 run 中创建 worktree、加载 workflow、执行 stage、记录结果。
- 能区分 native workflow 执行和 LangGraph-compatible graph spec 的关系。
- 能解释 resume / retry / loop 这些工程机制为什么是 Agent 编排的核心。

### 源码 / 资料阅读任务

| 文件路径 | 看什么 |
| --- | --- |
| `src/muxdev/runtime/supervisor.py` | 看 `run`、`resume`、`_execute_workflow`、`_execute_native_workflow`、`_approval_gate` 的职责，不逐行读。 |
| `src/muxdev/workflows/engine.py` | 看 `load_workflow`、`validate_dag`、`ordered_stage_ids`、`execution_batches` 如何把配置变成可执行 DAG。 |
| `src/muxdev/config/defaults/workflows.yaml` | 重点读 `software-dev` 的 stage 编排：task_intake、plan、approve_plan、implement、test、review、delivery_verify、fix。 |
| `src/muxdev/models/__init__.py` | 看 `WorkflowStage`、`WorkflowDefinition`、`LoopPolicy` 和 run / stage 状态枚举。 |
| `src/muxdev/runtime/langgraph_engine.py` | 看 graph spec、nodes、edges、conditional_loop 如何表达 LangGraph 语义。 |
| `src/muxdev/services/orchestration.py` | 看 `workflow_to_langgraph` 和 `deep_agent_task_pack` 如何把 muxdev workflow 导出给外部编排系统。 |

### 课堂讲解要点

- Workflow 是 Agent 系统的“控制逻辑”，不是简单任务列表。
- stage 的 `role` 决定模型扮演的职责，`deps` 决定执行顺序，`type` 决定是 agent、human_gate 还是 delivery_gate。
- `SupervisorRuntime.run` 创建 run 目录、隔离 worktree、Blackboard、TraceWriter 和 SafetyPolicy。
- `_execute_native_workflow` 是主执行循环：跳过已完成 stage、判断 when、触发审批、调用 provider、记录 artifact。
- resume 先检查 pending approval 和 pending provider action，避免重复启动 provider 或覆盖状态。
- LangGraph 适配不是推翻现有 runtime，而是在保留证据、审批、黑板稳定性的前提下暴露图语义。
- `workflow_to_langgraph` 是讲“muxdev workflow 可以被图化和外部编排”的小切口。

### 面试表达点

- 我重点阅读了 SupervisorRuntime，理解它如何把一次自然语言任务拆成可恢复、可审批、可观测的 stage 执行过程。
- muxdev 的 Workflow 是 DAG 加条件循环，不只是顺序调用模型，这体现了 Agent 编排能力。
- 它通过 LangGraph-compatible spec 暴露图结构，同时保留本地 runtime 的审计和交付能力。

## 第 3 章：大模型接入核心：Provider Adapter 与 Prompt Contract

### 教学目标

- 能理解 provider adapter 如何把不同 AI Coding CLI 统一成 `run_stage` 调用。
- 能说明 Prompt Contract 如何约束 stage 角色、权限、输出格式和证据要求。
- 能解释 provider stream 解析如何识别登录、限流、确认提示等等待状态。
- 能把多模型接入能力对应到大模型应用开发中的抽象、兼容和稳定性设计。

### 源码 / 资料阅读任务

| 文件路径 | 看什么 |
| --- | --- |
| `src/muxdev/providers/adapters.py` | 看 `ProviderAdapter`、`MockProviderAdapter`、`HeadlessCliProviderAdapter`、`EVIDENCE_PROMPT_BLOCK`。 |
| `src/muxdev/providers/contracts.py` | 看 ProviderDescriptor、ProviderCapabilities、ProviderSession、ProviderRuntime 协议。 |
| `src/muxdev/providers/registry.py` | 看 provider detect / probe 如何描述 provider 可用性和能力状态。 |
| `src/muxdev/config/defaults/providers.yaml` | 看 codex、claude-code、qwen、mock 等 provider 如何通过配置定义 runtime command、transport 和 prompt_template。 |
| `src/muxdev/services/prompt_templates.py` | 看 `render_stage_prompt` 如何组合 preamble、stage template、role instruction、schema contract。 |
| `src/muxdev/config/defaults/prompt_templates.yaml` | 看 role prompt、schema prompt、stage prompt 如何构成 Prompt Contract。 |
| `src/muxdev/clients/stream.py` | 看 provider 输出如何解析为 `approval_prompt_detected`、`auth_error`、`rate_limit`、`idle_timeout`。 |

### 课堂讲解要点

- Provider Adapter 的作用是屏蔽不同 CLI 的命令参数、输入方式、输出格式和会话文件。
- Mock provider 不只是测试替身，也让学生在没有外部账号时理解完整生命周期。
- Headless CLI provider 会归档 transcript 和 chunks，这是后续 Provider Action 与 Evidence 的基础。
- Prompt Contract 分为 runtime contract、stage brief、role instructions、stage instructions、output contract。
- `EVIDENCE_PROMPT_BLOCK` 要求 provider 不只说 done，而是输出 claims、evidence、tests、missing_evidence、risks。
- StreamAdapter 把模型或 CLI 的阻塞文本转为结构化事件，让系统知道是登录、限流、确认还是超时。
- 大模型接入的难点不是“调 API”，而是稳定处理多 provider 的输入、输出、失败和等待。

### 面试表达点

- 我理解 muxdev 的 Provider Adapter 是多模型 / 多 CLI 的统一执行接口，能把不同 provider 纳入同一套 runtime。
- Prompt Contract 通过角色、权限、schema 和 evidence 要求约束模型输出，减少自由文本不可控问题。
- Stream 解析把外部 CLI 的等待状态结构化，这是工程化 Agent 系统稳定性的关键。

## 第 4 章：状态管理与可观测：TaskManager / Blackboard / Artifacts

### 教学目标

- 能说明 TaskManager 为什么是 daemon 侧 run 生命周期和写入边界。
- 能理解 Blackboard 如何用 SQLite 表承载 run、stage、agent、approval、provider_action、artifact、evidence 等事实。
- 能解释 artifacts、trace、contracts、session capsules 对可观测和可恢复的意义。
- 能把状态管理能力对应到大模型应用中的调试、审计、恢复和用户信任。

### 源码 / 资料阅读任务

| 文件路径 | 看什么 |
| --- | --- |
| `src/muxdev/daemon/tasks.py` | 看 `submit_task`、`continue_task`、`task_detail`、`approvals`、`provider_actions`、`_runtime`。 |
| `src/muxdev/storage/blackboard.py` | 看 schema 设计和核心方法：create_run、upsert_stage、add_artifact、create_approval、create_provider_action、replace_evidence_v2。 |
| `src/muxdev/storage/trace.py` | 看 trace JSONL 如何用于复盘、压缩和导出。 |
| `src/muxdev/storage/contracts.py` | 看 stage contract、role result contract、blind validator panel 如何落盘和 hash。 |
| `src/muxdev/storage/memory.py` | 看 memory proposal、query、approval、quarantine 如何和证据分层。 |
| `src/muxdev/services/session_capsules.py` | 看 provider 失败或等待时如何打包 handoff 信息。 |
| `src/muxdev/presentation/dashboard/view_model.py` | 看 UI 不是直接读日志，而是从 read model 汇总状态。 |

### 课堂讲解要点

- TaskManager 负责提交、继续、停止、查询、审批、Provider Action、报告、diff、rollback 等任务生命周期操作。
- Blackboard 是系统事实来源：run 状态、stage 状态、agent、artifact、usage、error、evidence 都可以查询。
- Artifacts 不是附属文件，而是交付链路中的可追踪产物，如 task.md、workflow.yaml、stage_output、diff.patch、evidence。
- TraceWriter 记录事件流，适合讲“为什么 Agent 系统需要可回放过程”。
- Memory 与 Evidence 分开：长期记忆需要治理，证据记录事实，不能把临时模型判断直接变成项目知识。
- Dashboard / TUI 展示的是结构化状态，不是把 provider 输出原样贴给用户。
- 应届生可以把这一章讲成“我理解了 Agent 系统如何从黑盒对话变成可观测工程系统”。

### 面试表达点

- 我重点理解了 TaskManager 和 Blackboard，知道 muxdev 如何把任务生命周期从临时进程变成可查询状态机。
- Artifacts、contracts、trace、session capsules 让一次 Agent 执行可以被复盘、调试和恢复。
- 这种状态设计体现了大模型应用开发中的可观测性和工程可靠性能力。

## 第 5 章：安全控制与可信交付：Approval / Provider Action / Evidence

### 教学目标

- 能区分 muxdev Approval 和 Provider Action 两类人工介入机制。
- 能说明 SafetyPolicy、approval subject hash、read-only gate、budget gate 如何降低失控风险。
- 能理解 Evidence v2 如何把交付结论变成事件流、manifest 和 evaluation。
- 能把可信交付机制对应到大模型应用岗位中的安全、合规、验证和用户信任能力。

### 源码 / 资料阅读任务

| 文件路径 | 看什么 |
| --- | --- |
| `src/muxdev/core/safety.py` | 看 SafetyPolicy 和 SafetyPolicyEngine 如何评估审批、shell、预算。 |
| `src/muxdev/runtime/policy/engine.py` | 看 runtime policy 的规则表达和决策输出。 |
| `src/muxdev/domain/approvals.py` | 看 ApprovalRequest 的最小契约。 |
| `src/muxdev/domain/provider_actions.py` | 看 ProviderActionRequest 如何表达 provider 侧等待。 |
| `src/muxdev/runtime/stage_attempt.py` | 看 provider attempt、failure kind、provider_actions_from_output 如何进入 runtime。 |
| `src/muxdev/services/evidence.py` | 看 `write_evidence_run`、`_collect_events`、`_build_manifest`、`_build_evaluation`。 |
| `src/muxdev/models/evidence.py` | 看 EvidenceEvent、EvidenceManifest、EvidenceEvaluation 的字段设计。 |
| `src/muxdev/storage/ledger.py` | 看 hash ledger 如何形成可验证事件链。 |
| `src/muxdev/services/delivery_gate.py` | 看 delivery gate 如何把交付前检查结构化。 |

### 课堂讲解要点

- Approval 是 muxdev 自己的策略审批，例如 plan、write、shell、merge、external。
- Provider Action 是外部 provider CLI 卡住了，例如登录、限流、确认提示、终端阻塞。
- 两者分开是可信设计：muxdev 不假装替用户向外部 CLI 输入 yes/no。
- `_approval_gate` 使用 subject hash 判断已批准对象是否漂移，避免旧批准被复用到新内容。
- read-only stage 如果改了 worktree，会被识别为违反权限边界。
- Evidence v2 的核心是 events.jsonl、manifest.json、evaluation.json，而不是一段模型自评。
- Delivery gate、blind validator、ledger、reports 把“看起来完成了”转成“有证据可检查”。
- 这一章可以重点训练学生讲安全与可信交付，而不是只讲模型能力。

### 面试表达点

- 我能解释 muxdev 为什么把 Approval 和 Provider Action 分开：一个是系统策略审批，一个是外部 provider 会话等待。
- Evidence v2 通过事件链、manifest 和 evaluation 记录交付可信度，避免只依赖模型自然语言承诺。
- 这体现了我对大模型应用中安全控制、人类在环和可信交付的理解。

## 第 6 章：源码综合串讲与面试表达

### 教学目标

- 能把 muxdev 从项目背景、架构、核心源码、可信交付串成完整项目故事。
- 能将源码阅读转化为简历项目描述，而不是声称自己开发了 muxdev。
- 能用 1 分钟说明 muxdev 为什么不是普通 ChatGPT wrapper。
- 能准备 3 类面试追问：架构追问、源码追问、岗位能力追问。

### 源码 / 资料阅读任务

| 文件路径 | 看什么 |
| --- | --- |
| `docs/source_walkthrough.md` | 按模块复盘源码，不陷入单文件细节。 |
| `docs/trusted_delivery.md` | 复盘可信交付、Evidence v2、contracts、ledger、rollback 的讲法。 |
| `docs/runtime_safety_provider.md` | 复盘 provider attempts、provider actions、session capsules、read-only gates。 |
| `docs/langgraph_loop_engineering.md` | 复盘 LangGraph-first runtime 方向、loop event 和 validation signal。 |
| `tests/test_runtime_m1_m4.py` | 看测试如何覆盖 runtime 主流程，理解“源码能力如何被验证”。 |
| `tests/test_provider_adapters_m5.py` | 看 provider adapter 和 provider action 的测试关注点。 |
| `tests/test_evidence_v2.py` | 看 Evidence v2 的 manifest、evaluation、hash chain 如何被验证。 |
| `tests/test_stream_workflow_safety.py` | 看 stream 解析、workflow safety 的边界测试。 |

### 课堂讲解要点

- 用“背景问题 → 控制面价值 → 架构分层 → Runtime 编排 → Provider 接入 → 状态证据 → 安全交付”的顺序串讲。
- 简历中表述为“深入阅读并分析 muxdev 开源项目”，不要表述为“主导开发 muxdev”。
- 源码讲解要抓 5 个关键词：SupervisorRuntime、Workflow、Provider Adapter、Blackboard、Evidence。
- 面试遇到“你做了什么”时，回答源码分析产出、架构图、模块职责梳理、面试表达总结。
- 面试遇到“为什么不是 wrapper”时，回答它管理状态、审批、Provider Action、证据、回滚和可观测。
- 面试遇到“对岗位有什么帮助”时，回答大模型应用开发不仅要会调用模型，还要会设计可靠的执行系统。
- 最后要求学生用 1 分钟项目介绍完成一次口头演练。

### 面试表达点

- 我通过源码阅读把 muxdev 拆成控制面、runtime、provider adapter、状态管理、安全交付几部分，并能讲清模块职责。
- 这个项目帮助我理解大模型应用工程岗位需要的能力：Agent 编排、模型适配、状态治理、证据评估和安全控制。
- 我可以从源码路径和测试文件出发回答面试追问，而不是只背项目介绍。

## 1 分钟项目介绍

我深入阅读并分析了 muxdev 这个开源项目。它不是普通的 ChatGPT wrapper，而是一个 local-first AI Coding Agent 控制面，目标是把 Codex、Claude Code、Qwen、mock 等不同 provider CLI 纳入统一的工程化交付流程。它的核心是 Runtime / Workflow：由 SupervisorRuntime 按 Workflow DAG 编排 plan、implement、test、review、delivery gate 等阶段，并支持恢复、审批、循环修复和 artifact 落盘。大模型接入层通过 Provider Adapter 把不同 CLI 抽象成统一的 run_stage，同时用 Prompt Contract 约束角色、权限、输出 schema 和 evidence 要求。安全上，它区分 muxdev Approval 和 Provider Action：前者是系统策略审批，后者是外部 provider 登录、限流或确认等等待状态。交付上，它用 Evidence v2、ledger、contracts、report、diff 等证据说明任务是否可 review、是否有风险、如何回滚。通过这个项目，我理解了工程化 Agent 系统不只是调用模型，而是围绕状态、编排、安全和可信交付构建完整控制面。

## 简历项目描述

深入阅读并分析 muxdev 开源项目，梳理 AI Coding Agent 控制面 Runtime/Workflow、Provider接入、Approval/Provider Action 与 Evidence，沉淀源码导览和架构讲解，提升大模型应用开发 Agent 编排、模型接入与可信交付理解。
