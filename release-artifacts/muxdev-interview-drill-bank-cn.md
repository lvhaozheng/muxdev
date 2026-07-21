# muxdev 面试问题族与回答要点（130 题）

> 本题库与 `muxdev-teaching-playbook-cn.md`、`interview-qa-cn.md` 配套使用。它不是背诵稿：每个“回答要点”是最低事实集合，正式回答仍应使用“结论—问题—机制—代码—边界”结构。

## 使用方法

每轮随机抽 20 题，每题先做 60 秒回答，再由导师从“为什么、如何验证、失败怎么办、为什么不用替代方案、当前边界”中随机追问一次。答对主问题但无法回答追问，只计半题。任何把待完成能力说成已实现、伪造个人贡献或把 Provider 声明当 Runtime 事实的答案，该题计零分。

## P. 产品价值与竞品定位（P01～P10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| P01 | 已有 Codex、Claude Code、Cursor，为什么还需要 muxdev？ | 前者主要执行或辅助编码；muxdev 统一跨 Provider 的工作流、隔离、取证、门禁与恢复。它不替代模型，而是补交付控制面。 | `README.md`、Q1 |
| P02 | 这不就是再包一层 CLI 吗？ | Wrapper 只转发命令；muxdev 冻结 Workflow/Policy、持久化 Stage、重放检查、绑定 Subject、验证 Reviewer 独立性并重算 Gate。 | `RunEngine.run`、`evaluate_gate` |
| P03 | Claude Code 也有 Transcript、Memory、Hooks，有何区别？ | Transcript 记录会话，Memory 提供未来上下文，Hooks 约束生命周期；muxdev 的 Typed Evidence 与冻结 Policy 可独立复算交付结论。二者可组合，不是互斥。 | Q2、Q5、Q19 |
| P04 | muxdev 的目标用户是谁？ | 需要复用多个 Coding Agent、对高风险变更做独立验证、保留审计和恢复事实的个人或小团队；不是只要一次代码补全的用户。 | `README.md`、`open-source-research.md` |
| P05 | muxdev 的最小可落地价值是什么？ | 即使只有一个 Provider，也能把一次变更变成隔离执行、Runtime 检查、结构化 Evidence 和确定性 Gate；多 Provider 是增强，不是价值前提。 | lite `change` Policy、`RunEngine` |
| P06 | 相比 OpenHands、Aider、SWE-agent 的差异是什么？ | 它们分别偏 Agent 平台、Pair Programming 或 Agent-Environment 执行；muxdev 聚焦跨 Worker 的可信交付事实与门禁。只比较职责，不声称全面更强。 | `open-source-research.md` |
| P07 | 为什么称为“控制面”？ | 它定义生命周期、路由、策略、证据和交付决策；实际编码由 Provider Worker 执行，类似控制面与数据/执行面的职责分离。 | `TaskService`、`ProviderAdapter` |
| P08 | 本地优先带来什么价值？ | 代码、SQLite 状态、Worktree、Artifact 和 Evidence 默认在本机，便于隐私、离线复验和用户掌控；不等于完全离线，外部模型仍可能联网。 | `ControlStore`、`WorktreeManager` |
| P09 | 项目最大的竞争壁垒是什么？ | 不是模型数量，而是经过对抗测试的不变量：Runtime ground truth、Subject binding、独立审查、恢复边界和可复验 Gate。 | `tests/contract`、`tests/integration` |
| P10 | 什么场景不应该用 muxdev？ | 一次性低风险补全、无需审计的小脚本，或需要高度动态通用 Agent 图而不关心交付证据时，额外控制面成本可能不划算。 | Profile 设计、边界说明 |

## A. 系统架构与生命周期（A01～A10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| A01 | 一次任务从提交到交付经过什么？ | TaskService 创建 Run；RunEngine 冻结配置并准备 Worktree；按 DAG 执行 Stage；记录 Evidence；Gate 决策；通过后应用变更并写报告。 | `TaskService`、`RunEngine.run/_finalize` |
| A02 | TaskService 和 RunEngine 为什么分开？ | TaskService 是用例和多入口生命周期边界；RunEngine 是执行、恢复、Evidence 与副作用边界，避免 CLI/HTTP/MCP 各写一套逻辑。 | `application/task_service.py` |
| A03 | 四个 Workflow 分别是什么？ | `change/design/review/test`；用固定流程覆盖核心交付意图，避免动态生成不可审计流程。 | `workflows.yaml` |
| A04 | 三档 Profile 有什么区别？ | lite、standard、strict 逐步提高计划、独立/安全审查、人工批准、超时、重试和成本要求；不改变 Provider 对事实的权威。 | `PROFILE_SETTINGS`、`evidence_policies.yaml` |
| A05 | 为什么 Workflow 和 Policy 要冻结？ | 若运行中配置变化，恢复后的同一 Run 会得到不同阶段或 Gate；冻结副本保证可重放语义。 | `RunEngine.run` metadata、`_execute` |
| A06 | Run、Job、Stage、Event 各是什么？ | Run 是完整任务；Job 是一次 execute/resume 尝试；Stage 是 DAG 节点状态；Event 是 append-only 事实序列。 | `ControlStore` 表与方法 |
| A07 | 系统的唯一事实源是什么？ | SQLite 事实表加运行 Artifact；Evidence Report 是冻结导出，Gate 从 Policy 与 Typed Records 派生。Memory、UI 投影和 Provider 输出都不是门禁事实源。 | `storage/control.py`、`evidence_verify.py` |
| A08 | CLI、HTTP、MCP 是否有不同语义？ | 表面协议不同，但共享 TaskService/ControlStore 生命周期；当前固定为 30/18/8 个公开表面。 | `cli/main.py`、`api/web.py`、`api/mcp.py` |
| A09 | 最重要的架构边界是什么？ | Provider 只产生声明和修改；Runtime 负责环境事实、权限检测、Evidence 和 Gate。 | `ProviderAdapter.execute`、`RunEngine` |
| A10 | 为什么不保留原来的大平台架构？ | 功能广度造成多套交付权威和高维护成本；精简到可证明不变量，使项目更容易验证、解释和演进。 | `simplification-report.md`、提交 `db520a6` |

## V. Provider Adapter 与多 CLI（V01～V10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| V01 | Provider 的输入统一了吗？ | 统一到 `StageExecutionInput` 的语义层；命令行语法、Prompt 传输和输出事件仍由各 Codec 处理，不能强行完全统一。 | `domain/stage.py`、Q10 |
| V02 | Codex 的 Prompt 如何传？ | 配置使用 stdin；Codec 构造 argv 时不再放 `{prompt}`，保证只有一个 Prompt 来源。 | `providers.yaml`、`build_cli_invocation` |
| V03 | Claude Code 与 Qwen 如何传？ | 两者配置把 `{prompt}` 作为 argument；Codec 校验模板中恰好一个占位符。 | `providers.yaml` |
| V04 | 为什么要显式 prompt_transport？ | 靠猜测会出现 Prompt 丢失、重复或被 CLI 当成选项；显式传输是 Provider Anti-Corruption Layer 的契约。 | `CliInvocation`、协议单测 |
| V05 | JSONL 为什么不能用一个通用正则解析？ | 不同 Provider 的事件类型、嵌套字段、文本增量和 session id 不同；正则容易吞事件或拼错内容。 | `parse_cli_output` |
| V06 | Provider 输出里保留哪些协议元数据？ | 归一化 content，同时保留 protocol、event_count、session_id、stdout/stderr 与退出码，方便诊断和恢复判断。 | `ParsedCliOutput`、`StageExecutionResult` |
| V07 | 新增一个 CLI 的步骤是什么？ | 注册配置与能力；选择 Prompt transport；实现/扩展输出 Codec；定义无交互模式；增加 conformance 测试；验证只读和失败语义。 | `providers.yaml`、`test_provider_protocols.py` |
| V08 | 为什么当前使用 headless CLI 而不是直接模型 API？ | 可复用用户已有 Coding Agent 的工具生态、上下文和编辑能力；代价是 CLI 协议不稳定、交互与恢复更难。 | `HeadlessCliProviderAdapter` |
| V09 | Provider Action 和 muxdev Approval 有何区别？ | Provider Action 是外部 CLI 会话请求；Approval 是 muxdev 对 Workflow Checkpoint 的政策决定。两者身份、Subject 和恢复协议不同。 | Q17、`interaction_requests` |
| V10 | Provider Action 当前为什么未完成？ | Adapter 使用一次性 `subprocess.run`，缺少长连接 Session Port、流式请求持久化和回送通道；字段预留不等于实现。 | `adapters.py`、Q17 |

## D. Supervisor DAG 与多 Agent（D01～D10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| D01 | 常见多 Agent 协作模式有哪些？ | 顺序链、路由、Fan-out/Fan-in、Orchestrator-Workers、Evaluator-Optimizer、群聊/Swarm、黑板与分层管理；muxdev 选择固定 DAG + 受限并行 + 修复循环。 | Q11 |
| D02 | Orchestrator-Workers 的核心是什么？ | Orchestrator 分解、授权、调度和归并；Worker 只完成受限 Stage。难点是状态、权限、终止、一致性与可验证归并，不是调用多个模型。 | `RunEngine`、Q12 |
| D03 | execution_waves 如何工作？ | 用 Kahn 拓扑算法反复取入度为零的稳定 Frontier；先校验缺失依赖与环，顺序可复现。 | `workflows/engine.py` |
| D04 | strict change 的并行点在哪里？ | test 之后，普通 review 与 security_review 共享依赖和冻结 Subject，作为同一 Wave 只读并行。 | `workflows.yaml`、`test_workflow_dag.py` |
| D05 | 为什么写 Stage 不并行？ | 共享 Worktree 上并行写会出现覆盖、冲突和不可确定归并；当前只支持读共享 Subject。 | `_run_wave` 条件 |
| D06 | Fan-in 怎么保证确定性？ | Provider 调用可并发，但结果由主线程按 Workflow Stage 固定顺序提交 Evidence 和上下文。 | `_execute_readonly_fanout` |
| D07 | 如何防止 Agent 死循环？ | 条件表达式受限；fix 循环最多两轮；失败、成本和 Gate 都可终止；进展以 Runtime 事实而非 Agent 自述判断。 | `_repair_change`、`loop < 2` |
| D08 | Repair 后旧 Review 为什么不能复用？ | 修复改变 Subject Digest，旧 Review 的 target_digest 不再匹配；必须重新 test/review。 | `_repair_change`、Gate target 检查 |
| D09 | 为什么称“小型 Supervisor”而非框架？ | 只服务四个固定 Workflow、受限条件与一个只读 Frontier，不提供任意节点、Reducer、动态 Send 或插件 Checkpointer。 | `workflows.yaml`、Q15 |
| D10 | 如果未来要并行写，如何演进？ | 每 Worker 独立 Worktree；生成内容寻址 Patch；声明写集；做冲突检测和确定性 Reducer；归并后重新测试与审查。 | 当前边界、Q13 |

## E. Evidence、Gate 与可信交付（E01～E10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| E01 | Evidence 与日志/Transcript 有何区别？ | Transcript 是会话记录；Evidence 是有类型、生产者、Requirement、Subject、完整性和结果语义的可验证事实。 | `models/evidence.py`、Q2 |
| E02 | 五类 Evidence 是什么？ | Artifact、Check、Review、Interaction、Runtime，分别证明产物、检查、审查、人工决定和运行状态。 | `EvidenceRecord` 子类 |
| E03 | EvidencePolicy 的作用是什么？ | 用稳定 Requirement ID 指定接受类型、Subject 选择、完整性、可复现和独立性要求；是唯一硬门禁规则源。 | `evidence_policies.yaml` |
| E04 | Gate 有哪三种结果？ | 无 Blocker 为 PASS；硬要求失败/缺失为 BLOCKED；必需人工交互 pending 为 WAITING_HUMAN。 | `evaluate_gate` |
| E05 | Scorecard 与 Gate 为什么分离？ | Scorecard 解释 completeness、reproducibility、integrity、independence；不能覆盖 required evidence 的硬失败。 | `_scorecard`、Q18 |
| E06 | 模型说测试通过但退出码非零怎么办？ | CheckEvidence 同时保存 declared 与 observed；不一致直接失败，真实退出码优先。 | `_evaluate_requirement` |
| E07 | 如何保证 Review 针对最终代码？ | ReviewEvidence 的 `target_digest` 必须等于交付 Subject Digest；否则 Gate 失败。 | `ReviewEvidence`、Gate |
| E08 | 人工批准如何进入证据？ | Interaction 是 append-only 状态；Gate 只看同 Requirement/Stage 的最新决定，pending、approved、rejected 语义不同。 | `_latest_interactions` |
| E09 | Evidence Report 如何独立复验？ | 重新解析 Pydantic Schema、重算 Gate、Records Hash、Artifact Digest；有 Store 时再验事件链和绑定的 Head。 | `verify_evidence_report` |
| E10 | DSSE 在这里做什么？ | 可选 Envelope 包装引用报告摘要的 Statement；不复制业务事实，不自动代表标准合规或可信签名身份。 | `services/dsse.py` |

## W. Worktree、权限与运行时安全（W01～W10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| W01 | Git Worktree 为什么有价值？ | 每个 Run 有隔离路径和分支，可计算独立 Diff、复用恢复目录，并避免直接污染主工作区。 | `WorktreeManager.prepare` |
| W02 | 只调用 `git worktree add` 算技术亮点吗？ | 不算；亮点是围绕碰撞、Fallback、恢复复用、Subject、越权写检测和安全回写建立完整不变量。 | Q16 |
| W03 | 非 Git 仓库怎么办？ | 复制 Workspace 到 Run 目录，初始化 Git 基线，以便仍能计算状态和 Diff。 | `workspace_copy` 分支 |
| W04 | Git worktree 创建失败怎么办？ | 不删除未知路径；受控复制并记录策略和错误，建立 fallback baseline。 | `git_worktree_fallback_copy` |
| W05 | 如何避免复制 `.muxdev` 导致递归？ | Ignore 回调排除 `.git`、`.muxdev`、测试缓存、Run 目录和看起来像 muxdev home 的路径。 | `_fallback_copy_ignore` |
| W06 | Reviewer 修改代码怎么办？ | 调用前后比较 Subject；read_only Stage 一旦变化，记录 Runtime failure，结果不应用主 Workspace。 | `_commit_stage`、集成测试 |
| W07 | 测试命令生成文件是否总是违规？ | 当前交付 Subject 发生变化即完整性失败；合法生成物应放被忽略临时目录或未来由 Policy 显式声明写集。 | `_run_check` |
| W08 | Worktree 等于安全沙箱吗？ | 不等于。它隔离 Git 工作目录，但进程网络、系统文件和凭据权限仍取决于 Provider CLI、OS 和 Sandbox 配置。 | `providers.yaml`、边界说明 |
| W09 | 最终变更如何回到主 Workspace？ | 只有 Gate 允许且完整性满足时才通过 Runtime 的 apply 逻辑回写；只读违规或检查夹带修改不会被应用。 | `runtime/workspace.py::apply_changes` |
| W10 | 能否精准回滚任意 Evidence 节点？ | 当前不能。Evidence 可定位阶段与 Digest；恢复跳过已完成 Stage并复用 Worktree，但任意节点 Snapshot/Time Travel 未实现。 | Q6、Q16 |

## M. Memory、RAG 与上下文工程（M01～M10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| M01 | 为什么 Agent 需要 Memory 治理？ | 未治理总结可能错误、过期、含敏感信息或污染未来决策；不能让模型自然语言自动晋升为项目事实。 | Q20 |
| M02 | 当前 Memory 从哪里来？ | 从已完成 Run 的 Evidence Report 派生，不建第二真相表；报告必须仍能验证且 Gate=PASS。 | `retrieve_verified_memory` |
| M03 | 为什么只召回 PASS 报告？ | 失败或被篡改的交付不应成为未来工作的可信经验；每次检索重新复验可处理后续 Artifact 漂移。 | `verify_evidence_report` 调用 |
| M04 | 当前 RAG 使用什么算法？ | BM25 词法排名，查询为当前 Task，文档为历史任务与结构化 Stage 摘要；最多 3 条且分数大于 0。 | `_bm25`、`retrieve_verified_memory` |
| M05 | 为什么不用向量数据库？ | 当前数据规模小、需要本地确定性和可重建性；BM25 足够且无新服务。语义召回不足时再引入 Embedding，并保留 Provenance。 | Q8、Q20 |
| M06 | Context Pack 有哪三段？ | 前序结构化 Stage 事实、确定性 AST Repo Map、Verified PASS 历史 Memory。 | `build_context_pack` |
| M07 | 12K 预算如何分？ | 总上限 12,000 字符；前序事实约 3,500，Repo Map 约 6,000，剩余给历史；所有内容裁剪并记录 Manifest。 | `services/context.py` |
| M08 | Repo Map 为什么用 AST？ | 路径和符号签名能在小预算内给全局结构；比全文复制更稳定。当前不做 Aider 的 PageRank。 | `services/repo_map.py` |
| M09 | Memory 能改变 Gate 吗？ | 不能。它只进入 Prompt；Gate 只读取冻结 Policy 与 Evidence Records，防止旧经验绕过当前检查。 | `adapters.py`、`gate.py` |
| M10 | Context 压缩的代价是什么？ | 字符预算不等于精确 Token；裁剪可能丢细节；BM25 不懂所有语义。Manifest、Digest 和来源 Run ID用于可解释。 | Q22 |

## R. 持久化、恢复与迁移（R01～R10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| R01 | 为什么需要持久化 Stage？ | 进程崩溃或人工等待后能知道哪些 Stage 完成、运行或失败，避免从头重复副作用。 | `ControlStore.upsert_stage` |
| R02 | resume 如何处理已完成 Stage？ | 读取 Store 中 completed 集合，在冻结 Workflow 中跳过对应节点，继续未完成 Wave。 | `RunEngine._execute` |
| R03 | 为什么不透明 Provider 的 running Stage 不能直接重跑？ | 可能已修改文件或调用外部工具，无法判断副作用边界；盲目重跑会重复执行。 | `RunEngine.resume` |
| R04 | Mock/Replay 为什么可以更安全恢复？ | 它们行为确定或有可复现记录，Runtime 能推断幂等语义；真实外部 CLI 默认不作此假设。 | `resume` 条件 |
| R05 | 事件链如何构造和验证？ | 每个事件包含 sequence、prev_hash、event_hash；验证重算顺序和前驱，并比较报告绑定的链头。 | `append_event`、`verify_event_chain` |
| R06 | 为什么 SQLite 适合当前系统？ | 本地优先、事务强、部署简单，足以承载单机 12 表事实模型；并发 Provider 结果由主线程提交降低写竞争。 | `ControlStore.transaction` |
| R07 | 并行 Worker 会不会并发写 SQLite？ | Provider 调用在线程池并发，结果收集后在主线程固定顺序写 Store，避免跨线程事务和非确定事件顺序。 | `_execute_readonly_fanout` |
| R08 | v7 迁移为什么不用原地 ALTER 完成？ | 大幅模型收敛时原地半失败难恢复；使用备份、临时库、计数/摘要验证和原子切换。 | `migrate_workspace`、迁移测试 |
| R09 | 旧 Evidence 如何处理？ | 作为 unscored legacy event 导入，保留历史但不伪造新 Schema 所需的独立性、Subject 或可重现语义。 | `simplification-report.md` |
| R10 | 恢复与回滚的区别是什么？ | 恢复继续同一 Run 并跳过已完成步骤；回滚恢复代码/状态到旧快照。当前主要实现前者，不宣称任意时间旅行。 | Q6、Q15 |

## T. 路由、Skill 与成本治理（T01～T10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| T01 | ProviderRouter 依据什么选择？ | 先做能力、认证、认证状态与成本过滤，再结合历史质量下界、延迟和成本；显式 Provider 仍需满足约束。 | `ProviderRouter.route/_candidates` |
| T02 | 为什么用 Beta 下界而不是平均成功率？ | 小样本平均值过度乐观；保守下界把不确定性计入路由，只有可信 PASS 结果进入历史。 | `services/router.py::_history` |
| T03 | 哪些运行能更新质量历史？ | Gate PASS、Evidence 完整性有效、Runtime 确认独立审查的结果；Provider 自报 confidence 不进入权威质量。 | `_finalize`、Router outcome |
| T04 | 什么是异构 Reviewer？ | Reviewer Provider/身份与执行者不同，用来降低同一模型系统性偏差和自审冲突。 | `role_providers`、independent evidence |
| T05 | strict 为什么可能因 Provider 不足而阻断？ | fail-closed：无法找到满足认证和独立审查的 Provider 时，不能降级伪装成严格交付。 | `ProviderRouter`、strict Policy |
| T06 | Skill 在系统中是什么？ | 角色化 Prompt 指导与权限声明的输入，不是门禁规则，也不能扩大 Stage 权限。 | `services/skills/`、README |
| T07 | 为什么移除 Skill 中的 Gate 权威？ | Markdown/TOML/Python 多处声明会造成冲突与不可复验；统一迁移到 EvidencePolicy。 | `services/skills/trust.py`、简化报告 |
| T08 | 如何处理 Skill 漂移？ | 发现、校验、信任状态与 lock digest 记录版本；旧 Delivery Standard 只给迁移警告。 | `skills/lock.py`、`skills/validation.py` |
| T09 | 成本如何限制？ | Profile 有上限；用户值被 `min` 收紧；Stage 累加 cost，超过预算记录 Runtime failure 并终止。 | `PROFILE_SETTINGS`、`_execute` |
| T10 | 路由可解释性如何体现？ | 保存 routing record，可通过 explain/replay/benchmark 查看候选、原因与历史统计，而不是只返回 Provider 名。 | `ProviderRouter.explain/replay/benchmark` |

## Q. 测试、对抗验证与质量（Q01～Q10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| Q01 | 为什么正常路径测试不够？ | Agent 系统的核心风险是伪造、漂移、越权和重放；必须有对抗测试证明 fail-closed。 | `tests/contract/test_adversarial.py` |
| Q02 | 当前测试覆盖哪些关键攻击？ | Provider 伪造测试、Artifact/事件链篡改、自审、Reviewer 越权写、测试命令改源码、Skill 提权和迁移失败回滚。 | `verification-report.md` |
| Q03 | 如何测试 Gate 的确定性？ | 给定同一冻结 Policy、Records 和 Subject 重算，结果除 evaluated_at 外必须与报告一致。 | `verify_evidence_report` |
| Q04 | 如何测试 CLI Prompt 只传一次？ | 对 stdin/argument 组合断言 argv 与 stdin 互斥；遍历配置检查每个 headless CLI 的占位符契约。 | `test_provider_protocols.py` |
| Q05 | 如何测试 Fan-out？ | 断言普通/安全 Review 同属一个 Wave，出现 started/completed 事件，结果按固定顺序且越权写入被阻断。 | `test_workflow_dag.py`、集成测试 |
| Q06 | 如何测试 Memory 治理？ | 先产生 PASS Run 能召回；篡改报告或 Artifact 后验证失败，检索结果应为空。 | `test_context.py` |
| Q07 | Mock Provider 能证明什么、不能证明什么？ | 能证明控制面、Schema、状态机、Evidence 和 Gate；不能证明真实模型质量、CLI 协议稳定或实际费用。 | README、`providers/mock.py` |
| Q08 | 为什么要做架构度量？ | 防止精简后再次膨胀；冻结行数、文件、最大函数、依赖方向和公开表面预算。 | `architecture_metrics.py` |
| Q09 | 34 个测试是否代表系统生产就绪？ | 只能证明当前列出的行为；外部 CLI 版本漂移、真实凭据、网络和大规模并发仍需 conformance、E2E 与长期运行验证。 | `verification-report.md` 边界 |
| Q10 | 如何为新风险增加验证？ | 先定义不变量和失败样例，再在最小层加单测/契约测试，最后加集成链路与 Release Artifact，避免只测实现细节。 | 测试分层目录 |

## O. 开源取舍与技术选型（O01～O10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| O01 | 为什么不用 LangGraph？ | 四个固定 Workflow 不需要通用图运行时；第二 Checkpointer 会与 Evidence/Stage Store 争夺恢复权威。保留 Frontier、Interrupt 和持久化语义。 | `open-source-research.md` |
| O02 | 历史上不是已经加过 LangGraph 吗？ | 是，提交 `4d49507` 引入；后续精简证明其运行时收益不足以抵消双层状态和范围膨胀，因此删除依赖而保留必要思想。 | Git 历史、`db520a6` |
| O03 | 什么时候应该重新引入 LangGraph？ | 出现动态 Send、复杂 Reducer、任意图、跨进程长等待或生态 Checkpointer 需求，并能明确谁是唯一恢复权威时。 | 设计边界 |
| O04 | 从 OpenHands 学了什么？ | Typed append-only events、来源与展示分离；未照搬对话 Memory 和观察者框架。 | `open-source-research.md` |
| O05 | 从 SWE-agent 学了什么？ | Agent 与执行环境分离，动作通过单一环境边界；muxdev 把 Provider 与 Worktree/Runtime facts 分开。 | `providers/contracts.py`、`runtime/worktree.py` |
| O06 | 从 Aider 学了什么？ | Repo Map 在预算内提供路径和符号全局上下文；未引入 PageRank 或持久向量索引。 | `repo_map.py`、`context.py` |
| O07 | 从 in-toto/SLSA 学了什么？ | Subject、Predicate、Envelope 和 Provenance 术语、摘要绑定；不声称合规等级。 | `dsse.py`、研究报告 |
| O08 | 为什么自研不是“重复造轮子”？ | 只有在需求固定、核心不变量少且第二运行时会增加权威冲突时才合理；否则应采用成熟框架。当前实现范围和非目标均明确。 | Q8、Q15 |
| O09 | 为什么不用向量数据库做 RAG？ | 本地小规模、可重建和确定性优先；BM25 足够。规模/语义需求增长时可替换检索层，但必须保留 PASS 与 Provenance 过滤。 | `services/context.py` |
| O10 | 为什么不直接用 Claude Hooks 做门禁？ | Hooks 绑定单个 Provider 生命周期且可被不同启动方式绕过；muxdev Policy 在 Provider 外统一执行。Hooks 可作为额外防线。 | Q19 |

## B. 简历、贡献与行为面试（B01～B10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| B01 | 你在项目中具体做了什么？ | 按真实身份回答：设计者讲决策与实现；二开者讲自己的 Diff；复现者讲实验。给出可验证提交、测试和产物。 | 教学手册身份矩阵 |
| B02 | 最难的技术问题是什么？ | 选择一个可深挖问题，如 Runtime ground truth、只读 Fan-out 或恢复；按症状、错误方案、当前方案、测试、代价回答。 | 难点矩阵 |
| B03 | 做错过什么设计？ | 可讲先引入 LangGraph和大平台功能，后发现多权威与范围膨胀；用度量和不变量驱动精简，而不是把删除包装成失败。 | Git 历史、简化报告 |
| B04 | 如何证明精简不是功能倒退？ | 对照用户核心问题与不变量；保留可信交付深度；用测试、接口预算、迁移和 Release Artifact 证明。 | `verification-report.md` |
| B05 | 如果重做一次会改变什么？ | 更早定义产品非目标、唯一事实源、Provider conformance 和对抗测试；Provider Action 会优先设计为 Session Port。 | 当前待完成边界 |
| B06 | 如何与导师/团队讨论技术选型？ | 先写决策问题与约束，列替代方案、触发条件、可逆性和验证指标；避免“因为流行所以用”。 | LangGraph 决策案例 |
| B07 | 项目体现哪些岗位能力？ | Agent 编排、模型/CLI 适配、上下文工程、状态机、持久化、测试、安全、可观测与产品取舍；每项必须绑定代码贡献。 | Resume bullets |
| B08 | 如何面对不会的问题？ | 明确当前代码不能证明；指出需检查的边界、拟增加的契约和测试，不猜测不存在的能力。 | 教学答题协议 |
| B09 | 简历数字如何保证可信？ | 从机器可读 metrics 和测试报告复算；代码变化后重跑，不使用旧 PPT 数字。 | `architecture-metrics.json` |
| B10 | 学生能否说自己主导 muxdev？ | 只有实际拥有设计/提交证据者可以；否则应说二次开发、复现或源码分析，并清楚区分原项目与个人贡献。 | 身份矩阵 |

## L. 登录接口综合场景（L01～L10）

| ID | 面试问题 | 回答要点 | 代码/材料锚点 |
|---|---|---|---|
| L01 | 开发登录接口会保存哪些 Evidence？ | Plan/Change Artifact、确定性 Check、普通与安全 Review、Interaction、Runtime 状态，并绑定任务、Stage、Producer 和 Subject。 | Q3、登录样例 |
| L02 | 登录测试通过就能交付吗？ | 不能只看测试；strict 还需计划、独立普通/安全审查、人工批准、完整性和 Runtime health。 | strict Policy |
| L03 | 安全 Review 应检查什么？ | 密码哈希、密钥、Token 生命周期、日志泄漏、暴力破解、用户枚举、Cookie/CSRF、时序和错误码；Finding 绑定 Digest。 | `default-secure/SKILL.md` |
| L04 | 模拟一条 CheckEvidence 要有哪些字段？ | requirement_id、stage、producer、subject_digest、argv、cwd、exit_code、stdout/stderr digest、duration、passed、reproducible、integrity。 | `CheckEvidence`、登录 JSON |
| L05 | 登录代码修复后能复用旧安全审查吗？ | 不能，Subject Digest 改变；旧 target_digest 失配，必须重新测试和安全审查。 | Gate target 检查 |
| L06 | 如果登录测试命令写了配置文件怎么办？ | 若改变交付 Subject，当前标记完整性失败；应写入临时/忽略目录或由未来策略声明允许写集。 | `_run_check` |
| L07 | 如果普通 Reviewer 和 Coder 是同一 Provider？ | strict/standard 独立审查 Requirement 不满足；Router 应选异构 Reviewer，否则 fail-closed。 | `independent_review` |
| L08 | 如何用登录样例演示篡改检测？ | 改 Artifact 文件或 JSON Record 后运行 verify；会出现 digest、records hash、Gate 重算或事件链错误。 | `login-evidence-example.json`、验证器 |
| L09 | 登录接口历史经验如何进入下次 Prompt？ | 只有报告仍验证为 PASS 才被 BM25 召回；摘要包含任务与结构化 Stage 事实，记录来源 Run 与分数。 | `retrieve_verified_memory` |
| L10 | muxdev 比长期记忆文件多做了什么？ | Memory 文件提供提示上下文；muxdev 还统一取证、绑定 Subject、验证独立性、重放检查、确定性 Gate、篡改复验和安全恢复。 | Q5、Q7 |

## 训练结果记录模板

```text
学生：
日期：
身份：设计者 / 二次开发 / 复现 / 源码研究
抽题 ID：
主问题正确：__/20
变体追问正确：__/20
边界误述：__ 次
无法定位代码：__ 次
最高质量答案：
需要返工的问题域：
下次门禁日期：
```

随机 20 题的最低毕业要求是 18 题主问题正确、16 题变体追问正确、边界误述为 0、核心代码定位成功率不低于 90%。
