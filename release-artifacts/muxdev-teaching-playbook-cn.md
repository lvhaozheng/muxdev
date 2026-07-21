# muxdev 项目教学与面试答辩规范（导师版）

> 适用基线：`release/v3` 当前工作树。本文是教学规范，不是营销材料。任何技术主张都必须能回到当前代码、测试、配置或可复验产物。

## 1. 教学目标与“99%”的正确含义

本课程面向准备大模型应用、AI Agent、后端工程和平台工程岗位的应届生。目标不是让学生背完 130 个标准答案，而是让学生掌握一套能覆盖问题变体的推理框架：面对一个陌生追问，能够从产品问题、信任边界、运行时事实、代码实现、工程取舍和当前限制六个角度组织答案。

“完成 99% 以上的面试官问题和回答”只能作为课程覆盖目标，不能作为对所有面试的统计保证。本规范把常见问题归纳为 13 个问题域、130 个问题族；每个问题族还能沿着“为什么、怎么做、失败怎么办、为什么不用另一方案、如何验证、当前还有什么限制”生成追问。学生通过问题族迁移能力覆盖自然语言变体，而不是记忆固定措辞。

课程完成后，学生必须能够：

1. 用 30 秒、2 分钟和 10 分钟三种长度介绍 muxdev，并保持事实一致。
2. 从登录接口示例讲清任务、DAG、Provider、Worktree、Evidence、Gate、Memory 和交付结果。
3. 打开代码，在 90 秒内定位关键类、函数、配置和对应测试。
4. 解释至少 12 个真实工程难点，包括错误方案、当前方案、代价和未完成边界。
5. 面对反对意见时不回避，例如“Claude Code 也能做到”“为什么不用 LangGraph”“这不就是脚本吗”。
6. 不夸大个人贡献，不把未实现能力、历史版本能力或规划中的能力说成当前事实。

## 2. 全课必须遵守的十条讲解规则

下面使用“必须、应该、禁止”表示教学约束。

### 规则一：先确定学生的真实贡献身份

学生在简历和面试中的措辞必须与证据匹配。

| 身份 | 可以说 | 必须提供的证据 | 禁止说 |
|---|---|---|---|
| 原始设计/开发者 | “我设计并实现了……” | Git 提交、设计记录、测试、发布产物 | 把团队工作全部说成个人完成 |
| 二次开发者 | “我基于 muxdev 实现了 X，并验证了 Y” | 自己的分支、Diff、测试与答辩记录 | “我从零主导了 muxdev” |
| 复现者 | “我复现了核心链路，并重写/扩展了 X” | 可运行复现、实验结果、复现报告 | 把原项目功能当作本人原创 |
| 源码研究者 | “我系统分析了 muxdev 的可信交付设计” | 架构图、源码笔记、演示和问题分析 | “我实现了该系统” |

导师是 muxdev 的实际设计和开发人员，可以讲第一手设计决策。学生只有在完成对应二次开发或复现实验后，才能把相应模块写成个人项目贡献。诚信不是措辞问题，而是面试官继续追问时能否拿出代码和验证结果的问题。

### 规则二：每个主张都使用“主张—证据—代码”三角形

学生不能只说“系统可靠”“支持多 Agent”“用了 RAG”。每个主张至少绑定三种材料：

- **主张**：系统具体保证什么，边界是什么。
- **运行证据**：测试输出、Evidence Report、事件、Diff、配置或度量。
- **代码锚点**：文件与稳定符号，而不是只背行号。

示例：

> 主张：严格变更工作流会并行执行普通审查和安全审查，但不并行写代码。证据：`tests/integration/test_run_engine.py` 验证 Fan-out 事件与只读越权写入阻断。代码：`workflows/engine.py::execution_waves` 计算 Frontier，`runtime/engine.py::_execute_readonly_fanout` 并发调用并按固定顺序提交结果。边界：当前不支持并行写 Worker 的自动 Patch 合并。

### 规则三：先讲问题，再讲技术名词

讲 LangGraph、BM25、DSSE、Worktree 或 DAG 前，必须先说它解决的风险。面试官关心的是“为什么”，不是技术栈清单。

- DAG 解决依赖顺序、并行机会和循环控制，不是为了简历上出现“多 Agent”。
- RAG 解决历史交付信息的预算化召回，不是为了增加向量数据库。
- Worktree 解决执行隔离和 Subject 冻结，不等于自动获得安全。
- DSSE 解决 Envelope 与报告摘要绑定，不代表项目自动达到 SLSA 等级。

### 规则四：把 Provider 声明与 Runtime 事实分开

这是全项目最重要的信任边界。Provider 可以声称“测试已通过”，但可信结论必须来自 Runtime 实际观察到的命令、退出码、工作目录、输出摘要、耗时和执行前后 Subject。

学生回答 Evidence、测试、评审、Memory、路由或 Gate 问题时，必须主动说明哪些字段是模型声明，哪些是 Runtime 事实。代码锚点为 `runtime/engine.py::_run_check`、`runtime/engine.py::_record_stage_evidence`、`services/gate.py::_evaluate_requirement`。

### 规则五：统一使用五层答案结构

每个技术问题按以下顺序回答：

1. **结论**：先用一句话直接回答。
2. **问题**：如果不这样做，会发生什么。
3. **机制**：系统通过哪些步骤处理。
4. **代码与例子**：给出稳定符号和一个具体场景。
5. **取舍与边界**：付出了什么代价，当前还没做什么。

这个结构简称 `C-P-M-C-B`（Conclusion、Problem、Mechanism、Code、Boundary）。30 秒回答保留第 1、3、5 层；2 分钟回答完整覆盖五层；白板深挖再补数据结构、失败路径和测试。

### 规则六：同一个概念必须能用登录接口落地

所有抽象概念都回到“实现一个用户登录接口”的连续案例：

- Plan 描述 API、密码校验、Token、错误码和测试计划。
- Implement 产生代码 Diff 和最终 Subject Digest。
- Test 记录命令、退出码和重放结果。
- Review 检查错误处理、边界条件和可维护性。
- Security Review 检查明文密码、时序攻击、Token 生命周期、日志泄漏和暴力破解。
- Approval 绑定被批准对象。
- Gate 根据冻结 Policy 和 Evidence 决定 PASS、BLOCKED 或 WAITING_HUMAN。

### 规则七：技术选型必须包含“为什么不用”

“用了什么”只能得到功能分；“为什么用、为什么不用、何时会切换”才能体现设计能力。例如当前没有把 LangGraph 作为运行时依赖，因为只有四个固定工作流、一个受限只读并行 Frontier 和最多两轮修复，引入通用 Checkpointer 会形成第二套恢复权威。若未来需要动态 `Send`、复杂 Reducer、跨进程长时间等待或任意图时，再重新评估。

### 规则八：当前能力、历史能力和规划能力必须分栏

讲解中出现下面三类句式：

- **当前已实现**：代码和测试都存在。
- **历史上做过但当前已删除**：用于说明演进，不得当作当前卖点。
- **待完成/演进方向**：明确方案和缺口，不得用现在时。

尤其需要主动声明：Provider Action 双向暂停/恢复目前未接通；证据链不等于任意节点快照回滚；并行写 Worker 尚不支持；Context Memory 是 BM25 派生视图，不是通用个人长期记忆；当前不依赖 LangGraph。

### 规则九：所有数字必须能复算

行数、文件数、接口数、表数和测试数只能引用最新验证产物。当前基线见 `release-artifacts/verification-report.md` 与 `release-artifacts/architecture-metrics.json`。代码变化后必须重新生成或重新验证，禁止继续背旧数字。

### 规则十：每次讲解都以反例或失败路径收尾

学生必须能回答“如果失败怎么办”。推荐每个模块至少演示一个反例：Provider 伪造测试成功、Reviewer 修改代码、测试命令夹带源代码修改、报告被篡改、审批缺失、历史 PASS 报告失去完整性、外部 Provider 在中断时状态不明。

## 3. muxdev 的唯一标准叙事

### 3.1 一句话定位

muxdev 是本地优先的 AI Agent 可信交付控制面：它复用 Codex、Claude Code、Qwen 等 Coding Agent 作为执行 Worker，自己负责固定 Supervisor DAG、隔离执行、运行时取证、确定性门禁和可恢复状态。

### 3.2 它不是什么

- 不是比 Codex 或 Claude Code 更强的代码生成模型。
- 不是通用聊天式多 Agent 框架。
- 不是只做命令转发的 CLI Wrapper。
- 不是依靠 Prompt 让模型自觉遵守流程。
- 不是已达到 in-toto/SLSA 合规等级的供应链认证产品。

### 3.3 30 秒介绍

> 我设计的 muxdev 不替代 Coding Agent，而是在它们外面增加可信交付控制面。Codex 或 Claude Code 负责写代码，muxdev 把任务拆成固定 DAG，在隔离 Worktree 中执行，重放测试并记录五类 Evidence，再由冻结的 EvidencePolicy 确定性判断是否可以交付。它的核心价值是把“模型说完成了”变成“系统能够恢复、验证、解释和审计这次交付”。

### 3.4 两分钟介绍骨架

1. **痛点**：Coding Agent 能写代码，但 Transcript、Memory 和提示词不能独立证明交付正确。
2. **边界**：Provider 输出是声明；Diff、命令退出码、Subject Digest、Reviewer 身份、人工决定和事件链是 Runtime 事实。
3. **执行**：四类固定工作流、三档 Profile、小型 Supervisor DAG、只读 Review Fan-out、最多两轮修复。
4. **可信**：五类 Evidence、唯一 EvidencePolicy、硬 Gate 与四维 Scorecard 分离。
5. **上下文**：12K 字符 Context Pack，只召回仍可复验为 PASS 的历史交付，Memory 不能改 Gate。
6. **取舍**：不引入 LangGraph 运行时，不支持并行写和任意节点回滚；Provider Action 标记为待完成。

### 3.5 十分钟白板顺序

```text
User / CLI / HTTP / MCP
          │
      TaskService                 生命周期入口
          │
      RunEngine                   冻结 Workflow / Policy，持久化 Job / Stage
          │
  execution_waves()               确定性 DAG Frontier
    ├── Human Gate
    ├── Serial write worker
    └── Read-only fan-out          Review + Security Review
          │
   ProviderAdapter                统一 Stage 语义
    ├── protocol codec            stdin / argv + JSONL 差异
    └── Coding Agent CLI
          │
  isolated Worktree               Diff / Subject / Runtime check
          │
  EvidenceRecord × 5              Artifact / Check / Review / Interaction / Runtime
          │
   EvidencePolicy + Gate          PASS / BLOCKED / WAITING_HUMAN
          │
 evidence-report.json             可复验；可选 DSSE Envelope
```

## 4. 课程地图：10 个单元、4 次门禁

建议总课时为 12～16 小时，分 10 个单元。2.5 小时只能作为项目导览，不能宣称完成深度答辩训练。

| 单元 | 主题 | 学习结果 | 代码主入口 | 必做演练 |
|---|---|---|---|---|
| 1 | 产品定位与竞品 | 说清“为何有 Coding Agent 仍需要控制面” | `README.md`、`open-source-research.md` | 反驳“Claude Code 已经够了” |
| 2 | 一次 Run 的生命周期 | 画出 run→job→stage→report | `application/task_service.py`、`runtime/engine.py` | 跟踪一个 lite run |
| 3 | Provider 反腐层 | 区分统一语义与协议差异 | `providers/contracts.py`、`adapters.py`、`protocols.py` | 同一 Prompt 映射到三种 CLI |
| 4 | Supervisor DAG | 解释 Frontier、Fan-out、Fan-in、修复循环 | `workflows/engine.py`、`workflows.yaml` | 手算 strict change 的执行波次 |
| 5 | Worktree 与 Runtime 边界 | 解释隔离、Subject、写集检查 | `runtime/worktree.py`、`workspace.py` | Reviewer 越权写入攻击 |
| 6 | Evidence 与 Gate | 从记录重算 PASS/BLOCKED | `models/evidence.py`、`services/gate.py` | 修改登录证据使 Gate 阻断 |
| 7 | Memory 与 Context | 解释 PASS-only BM25 和预算 | `services/context.py`、`repo_map.py` | 篡改历史报告后检索消失 |
| 8 | 恢复、存储与迁移 | 解释 append-only、事件链、恢复边界 | `storage/control.py`、`evidence_verify.py` | 中断 Provider 的安全恢复判断 |
| 9 | 路由、Skill 与成本 | 解释能力过滤、异构评审和权限边界 | `services/router.py`、`services/skills/` | 自审为何不能算独立审查 |
| 10 | 交付、简历与压力答辩 | 把事实组织成项目故事 | 全部 Release Artifacts | 45 分钟模拟面试 |

四次学习门禁：

- **G1 概念门禁**：不开代码，学生能解释产品问题、信任边界和完整链路。
- **G2 源码门禁**：随机给一个问题，学生能在 90 秒内定位实现和测试。
- **G3 故障门禁**：随机注入一个失败，学生能预测 Gate、Evidence 和恢复行为。
- **G4 答辩门禁**：完成 45 分钟压力面试，综合得分至少 85，且诚信与边界项不得失分。

## 5. 每个单元的统一教学卡片

导师讲任何模块前都填写下面七项，禁止直接从 API 列表开始讲：

1. **用户问题**：真实用户为什么需要它。
2. **失败模式**：没有它或做错时会怎样。
3. **不变量**：系统始终要保证什么。
4. **数据模型**：哪些对象承载事实。
5. **执行路径**：主调用链和状态转换。
6. **验证方式**：哪个测试、命令或 Artifact 能证明。
7. **代价与边界**：复杂度、性能、兼容性和未完成项。

以 Evidence 单元为例：

- 用户问题：如何证明这次登录接口真的测试和评审过。
- 失败模式：Transcript 可丢失、可混杂声明，模型也可能虚构成功。
- 不变量：Gate 只由冻结 Policy、Typed Records 和当前 Subject 导出。
- 数据模型：五类 `EvidenceRecord`、`EvidenceRequirement`、`GateDecision`、`EvidenceReport`。
- 执行路径：`_run_check` → `_record_stage_evidence` → `_finalize` → `evaluate_gate`。
- 验证方式：`verify_evidence_report` 重新计算 Gate、Record Hash、Artifact Digest 和事件链头。
- 代价与边界：Runtime 必须接管检查；不能只信 Provider；证据链不提供任意快照回滚。

## 6. 真实设计与开发难点矩阵

下面的内容是项目答辩的核心。学生至少掌握其中 12 项，导师本人应能展开全部项目。

| 难点 | 真实症状/错误方向 | 当前处理 | 代码或历史证据 | 仍有的代价/边界 |
|---|---|---|---|---|
| 产品范围失控 | 历史系统增长到 11 工作流、10 Skills、54 表、99 HTTP 路由、159 CLI 命令，多个模块都能决定“是否交付” | 收敛为 4 工作流、3 Profile、5 Skills、12 表和唯一 Gate | `simplification-report.md`；提交 `db520a6` | 精简会删除部分历史 UI 与通用扩展能力 |
| LangGraph 双重权威 | 历史提交 `4d49507` 引入 LangGraph；通用 Checkpointer 与 Evidence/Stage Store 可能同时解释恢复状态 | 保留 DAG Frontier、持久化、Interrupt 等必要语义，自研固定运行时，不保留 LangGraph 依赖 | `workflows/engine.py`、`runtime/engine.py`、Git 历史 | 动态图、任意 Reducer、时间旅行需要重新设计 |
| Provider 会“自证成功” | 模型返回 `passed=true` 或伪造 argv/exit code | Runtime 忽略模型命令，只执行冻结的策略命令，并由 ExecutedCheck 生成 CheckEvidence | `_run_check`、`_check_evidence`、对抗测试 | 未配置可信命令时证据为 unavailable 并失败关闭 |
| 评审对象漂移 | Reviewer 检查旧 Diff，后续代码已变化 | ReviewEvidence 绑定 `target_digest`，Gate 与最终 Subject 对比 | `models/evidence.py::ReviewEvidence`、`services/gate.py` | 每次修复后必须重新审查 |
| 自审伪独立 | 同一 Provider/身份既写代码又审核 | standard/strict 要求独立 Reviewer，路由选择异构 Reviewer，Gate 验证 `independent` | `services/router.py`、`evidence_policies.yaml` | 可用 Provider 不足时会 fail-closed |
| CLI Prompt 传输不一致 | Codex 可从 stdin 读，Claude/Qwen 常把 Prompt 放 argv；错误配置会丢 Prompt 或传两次 | `build_cli_invocation` 强制 exactly-one prompt source，配置显式 `prompt_transport` | `providers/protocols.py`、`providers.yaml` | argv 可能暴露长 Prompt 给进程列表，未来可优先协议 API |
| CLI 输出协议漂移 | Codex、Claude 的 JSONL 事件结构、内容字段和 session id 不同 | Provider Codec 分别归一化，保留 protocol、event_count、session_id | `parse_cli_output`、`test_provider_protocols.py` | 未知 CLI 只能走 generic parser，兼容性较弱 |
| Provider Action 阻塞 | 外部 CLI 可能登录、确认、限流或要求工具审批；一次性子进程无法可靠暂停再回送 | 当前 headless 模式避免交互，字段预留但明确标记待完成 | `StageExecutionResult.interaction_requests`、Q17 | 需要流式双向 Session Port；当前不是已完成能力 |
| DAG 并行一致性 | 并发 Worker 可能看到不同 Subject、相互覆盖或以不同顺序写事件 | 只并行只读 Stage；Fan-out 前共享冻结 Subject；主线程固定顺序 Fan-in | `execution_waves`、`_execute_readonly_fanout` | 当前不支持并行写和 Patch 自动合并 |
| Reviewer 越权写入 | 声称只读的 Reviewer 可能修改 Worktree | Provider 调用前后比较 Subject；变更即 Runtime failure，且不回写主工作区 | `_prepare_stage`、`_commit_stage`、集成测试 | 文件系统级沙箱仍依赖底层 CLI/OS 能力 |
| 测试命令夹带修改 | 测试命令退出 0，但偷偷改源代码 | 检查前后快照 Subject；若修改，`integrity_valid=false` 并阻断应用 | `_run_check`、集成测试 | 部分合法生成型测试需要显式策略或临时目录 |
| 修复死循环 | Reviewer 反复提出问题，Coder 与 Reviewer 无终止条件 | 固定 `fix → test → review/security_review`，最多两轮，以 Runtime 反馈判断进展 | `_repair_change`、`when: loop < 2` | 两轮后仍失败就 BLOCKED，需要人工处理 |
| 崩溃恢复与副作用重放 | Stage 状态显示 running，但外部 CLI 可能已经产生一半副作用 | 完成 Stage 跳过；Mock/Replay 可安全继续；不透明 Provider running 状态要求 reconciliation | `RunEngine.resume` | 尚无 Provider 原生幂等键和双向会话恢复 |
| Worktree 边缘情况 | 分支名/路径碰撞、非 Git 仓库、复制递归、Windows 隐藏窗口、临时文件污染 | Git worktree 优先；失败时受控复制、建立基线、忽略运行目录并复用持久 Worktree | `runtime/worktree.py`、`core/platforms.py` | Fallback 复制成本高；清理策略仍需运维治理 |
| Memory 污染 | 模型随口总结被长期保留，旧失败经验被当成事实 | Memory 是可重建派生视图，只读取仍能完整性复验且 Gate=PASS 的历史报告 | `retrieve_verified_memory` | BM25 只有词法召回；不能理解所有语义同义词 |
| 上下文无限增长 | 全仓、全历史、全部对话塞给模型会变贵且稀释关键信息 | 12K 字符预算：前序结构化事实、AST Repo Map、最多 3 条 PASS 历史；生成 Manifest 与 Digest | `build_context_pack` | 固定字符预算不是模型 Token 精确预算 |
| Policy 漂移 | 运行中修改全局规则，恢复后同一 Run 得出不同结论 | 创建 Run 时冻结 Policy 与 Workflow 到 metadata；恢复使用冻结副本 | `RunEngine.run`、`RunEngine._policy` | 策略升级只影响新 Run，旧 Run 需要显式迁移 |
| 分数掩盖硬失败 | 质量分很高但人工批准缺失，若只看总分会错误交付 | Gate 与 Scorecard 分离；缺失 required evidence 一定 BLOCKED/WAITING_HUMAN | `services/gate.py`、`evidence-v3-example.json` | 使用者必须理解 N/A 与分母，不可只看 overall |
| 事件/Artifact 被篡改 | JSON 报告可被手工修改，产物文件可被替换 | 重算 Gate、Records Hash、Artifact SHA-256、Event Hash Chain 与报告绑定的 Chain Head | `verify_evidence_report`、`ControlStore.verify_event_chain` | 本地管理员仍可同时改库和文件；强身份签名需额外密钥治理 |
| DSSE 事实重复 | 若 Attestation 再复制一套业务字段，会形成第二事实源 | DSSE Statement 只引用 Evidence Report Digest，报告仍是业务事实 | `services/dsse.py` | 这是 in-toto 风格分层，不宣称标准合规等级 |
| v7 数据迁移 | 直接原地改 54 表数据库，失败可能留下半迁移状态 | 备份→临时库导入→计数/摘要校验→原子切换，失败回滚 | `storage/control.py::migrate_workspace`、迁移测试 | 旧 Evidence 作为 unscored legacy 事实，不能凭空补足新语义 |

## 7. 一次登录接口的标准课堂演示

### 7.1 演示任务

```text
实现 POST /api/login：校验邮箱与密码，成功后返回短期 access token；
错误凭证统一返回 401；不得记录明文密码；添加成功、失败、锁定和过期测试。
```

### 7.2 导师必须演示的八个节点

1. **冻结输入**：展示 task、workflow、profile、Policy Hash、Workflow Definition 和 Provider Route。
2. **计划**：Plan 必须包含数据边界、认证流程、异常、测试和安全假设。
3. **实现**：展示 Worktree、Diff、Affected Paths 和新的 Subject Digest。
4. **测试**：展示 Runtime 实际执行的 argv、exit code、stdout/stderr 摘要与可重现标志。
5. **普通评审**：检查业务正确性、错误处理、可读性和测试覆盖，并绑定 Digest。
6. **安全评审**：检查密码哈希、Token、日志、暴力破解、用户枚举和密钥管理。
7. **人工决定**：说明批准的是某个 Subject/Checkpoint，而不是笼统的“同意 Agent”。
8. **Gate 与报告**：删除一条证据、篡改 Artifact 或制造自审，重新计算并观察阻断原因。

### 7.3 学生必须说出的差异

Claude Code 可以生成代码、运行命令、保留 Transcript、使用 Hooks 或 Memory；muxdev 更多做的是统一 Stage 契约、冻结 Policy、隔离 Worktree、以 Runtime 事实取证、绑定评审对象、检查 Reviewer 独立性、自动重算 Gate、形成统一 Report，并在恢复时拒绝不安全重放。这是执行工具与交付控制面的职责差异，不是“谁更聪明”。

## 8. 代码阅读规范

### 8.1 三遍阅读法

- **第一遍：对象图**。只找 Run、Stage、Provider、Evidence、Policy、Gate、Subject、Interaction。
- **第二遍：成功路径**。从 `TaskService` 进入 `RunEngine.run`，跟到 `_finalize`。
- **第三遍：失败路径**。分别跟 Provider 非零退出、检查失败、只读写入、审批等待、报告篡改和恢复冲突。

### 8.2 禁止逐行朗读

学生讲源码必须先说函数的职责、不变量和输入输出，再选 5～15 行关键逻辑。面试官问“这段代码做什么”时，先回答它在系统链路中的位置，再解释局部实现。

### 8.3 必须熟悉的 20 个稳定锚点

| 主题 | 代码锚点 |
|---|---|
| 生命周期入口 | `application/task_service.py::TaskService` |
| 运行入口/恢复 | `runtime/engine.py::RunEngine.run/resume` |
| 冻结工作流 | `runtime/engine.py::_execute` |
| DAG 校验/Frontier | `workflows/engine.py::validate_dag/execution_waves` |
| Fan-out/Fan-in | `runtime/engine.py::_execute_readonly_fanout` |
| 修复循环 | `runtime/engine.py::_repair_change` |
| Stage 输入 | `domain/stage.py::StageExecutionInput` |
| Provider 统一入口 | `providers/contracts.py::ProviderAdapter` |
| CLI 调用 | `providers/adapters.py::HeadlessCliProviderAdapter` |
| Prompt/输出协议 | `providers/protocols.py::build_cli_invocation/parse_cli_output` |
| Worktree | `runtime/worktree.py::WorktreeManager.prepare` |
| Diff/应用 | `runtime/workspace.py::diff_text/apply_changes` |
| Evidence 模型 | `models/evidence.py::EvidenceRecord/EvidenceReport` |
| Runtime Check | `runtime/engine.py::_run_check` |
| Gate | `services/gate.py::evaluate_gate` |
| 独立复验 | `services/evidence_verify.py::verify_evidence_report` |
| Context Pack | `services/context.py::build_context_pack` |
| PASS-only Memory | `services/context.py::retrieve_verified_memory` |
| 路由 | `services/router.py::ProviderRouter.route` |
| 事实存储/事件链 | `storage/control.py::ControlStore` |

## 9. 面试答题协议

### 9.1 听题先分类

在 3 秒内判断问题属于：价值、架构、实现、可靠性、安全、数据、性能、选型、失败恢复、测试、演进、个人贡献或场景题。分类后从对应问题域调用事实，不要被面试官措辞带偏。

### 9.2 先回答，再铺垫

错误示范：“这个问题要从 AI Agent 的发展说起……”

正确示范：“不能完全替代。Claude Code 解决执行，muxdev 解决跨 Provider 的统一取证和确定性交付门禁。具体多出三层……”

### 9.3 使用可伸缩答案

- **15 秒**：结论 + 一个差异。
- **60 秒**：结论 + 机制 + 例子 + 边界。
- **3 分钟**：完整 `C-P-M-C-B` + 失败路径 + 测试证据。
- **白板**：数据模型 + 状态转换 + 并发/恢复不变量。

### 9.4 主动封住常见追问

答案结尾应包含一个真实边界。例如：“当前只并行只读 Review；并行写需要每个 Worker 独立 Worktree、Patch 冲突检测和可验证 Reducer，所以没有为了展示多 Agent 而提前实现。”这会把“没做”转化为工程判断。

### 9.5 不知道时的规范回答

> 当前代码没有证明这个能力，我不会把它说成已实现。按现有架构，我会先在 X 边界增加 Y 契约，再用 Z 测试验证；需要进一步检查的文件是……

这比猜测 API 或编造线上数据更专业。

## 10. 评分规范（100 分）

| 维度 | 分值 | 满分表现 | 一票否决/严重扣分 |
|---|---:|---|---|
| 产品价值 | 10 | 能区分 Coding Agent 与可信交付控制面，并说明真实用户价值 | 只说“支持多模型” |
| 架构全链路 | 15 | 能从入口讲到报告，边界与状态源清楚 | 模块罗列，无调用链 |
| 代码深度 | 15 | 能定位稳定符号，解释输入输出和不变量 | 背行号、说不存在的类 |
| Evidence/Gate | 15 | 能从 Records 重算 Gate，区分硬门禁与 Scorecard | 把模型自评当 Evidence |
| DAG/并发/恢复 | 10 | 能解释 Frontier、只读 Fan-out、固定 Fan-in、两轮修复和不透明恢复 | 宣称支持任意并行/任意回滚 |
| Provider/Context | 10 | 能解释协议反腐层、12K Context 和 PASS-only Memory | 把 CLI 参数说成完全统一或宣称向量库 |
| 故障与安全 | 10 | 至少分析三种对抗失败并给出代码证据 | 只讲成功路径 |
| 取舍与演进 | 10 | 能讲 LangGraph 引入又删除、范围精简与未来触发条件 | 用热门名词替代设计理由 |
| 表达与诚信 | 5 | 结论先行，贡献身份准确，边界主动说明 | 伪造个人贡献或把待完成说成已完成，直接不通过 |

毕业标准不是总分刚好 60，而是：总分至少 85；Evidence/Gate、代码深度、表达与诚信分别至少达到该项 80%；随机 20 个问题族正确完成至少 18 个；三次变体追问中至少两次能够迁移回答。

## 11. 训练安排

### 11.1 课前

学生提交：

- 一张手绘架构图；
- 一份术语表；
- 对 Q1、Q2、Q6、Q8、Q17 的初始回答；
- 自己的贡献身份和可证明材料。

### 11.2 课堂

每个单元按“10 分钟问题背景—20 分钟源码—15 分钟故障注入—10 分钟答辩”循环。导师每次至少问一个反对问题和一个边界问题。

### 11.3 课后

学生必须产出：

1. 30 秒、2 分钟、10 分钟三版录音或录像；
2. 登录接口 Evidence 讲解；
3. 一个自己实现或复现的改动及测试；
4. 从 130 个问题族中随机抽取 20 题的书面回答；
5. 一份“我不会在简历中声称什么”的边界清单。

### 11.4 间隔复习

- 第 1 天：产品、架构、Evidence。
- 第 3 天：Provider、DAG、Worktree。
- 第 7 天：Memory、恢复、路由、迁移。
- 第 14 天：全量压力面试。
- 第 30 天：只看问题不看答案，重新画图和讲解。

## 12. 导师提问规则

导师不能只问“是什么”，必须交替使用六类追问：

1. **反事实**：“不用这个设计会发生什么？”
2. **攻击**：“如果模型伪造/篡改/越权呢？”
3. **替代**：“为什么不用 LangGraph/向量库/Claude Hooks？”
4. **规模**：“从 2 个 Worker 扩到 20 个会怎样？”
5. **恢复**：“在这行副作用之后崩溃会怎样？”
6. **证据**：“哪段代码或哪个测试证明你的说法？”

导师应随机改变表述，不按题库顺序提问。一个答案如果只能匹配题库原句，视为没有掌握。

## 13. 常见错误与纠正话术

| 错误说法 | 为什么错 | 纠正说法 |
|---|---|---|
| “muxdev 比 Claude Code 更会写代码” | muxdev 不提供更强基础模型 | “muxdev 管理执行与交付证据，Worker 仍由 Coding Agent 完成代码任务。” |
| “所有操作都绝对完整记录” | 外部进程内部推理不可见；记录的是受管边界事实 | “所有受管 Stage、Artifact、Check、Review、Interaction 和 Runtime 事实遵循统一 Schema。” |
| “Evidence 可以回滚任意节点” | Evidence 是事实链，不是文件系统快照 | “Evidence 能定位节点和 Subject；当前恢复跳过完成 Stage，但任意快照回滚未实现。” |
| “我们用了 LangGraph” | 当前运行时没有 LangGraph 依赖 | “历史上引入过，当前保留所需图语义并使用固定 Supervisor。” |
| “RAG 用了向量数据库” | 当前是 BM25 + AST Repo Map | “使用 Evidence-grounded BM25，来源限制为仍可复验 PASS 的报告。” |
| “多 Agent 可以并行改代码” | 当前只并行只读 Review | “写 Stage 串行；并行写需要独立 Worktree 和合并协议，当前未实现。” |
| “Provider Action 已完成” | 一次性 `subprocess.run` 无双向恢复通道 | “muxdev Approval 已实现；Provider Action 是明确待完成项。” |
| “DSSE 证明符合 SLSA” | 只使用分层与摘要绑定思想 | “可选 DSSE 绑定报告摘要，不声明 SLSA 等级或 in-toto 合规。” |

## 14. 材料使用顺序

1. 先读本手册，建立教学规则与项目真相。
2. 再读 `interview-qa-cn.md`，掌握 Q1～Q22 的完整示范答案。
3. 使用 `muxdev-interview-drill-bank-cn.md` 做 130 个问题族训练。
4. 用 `login-evidence-example.json` 做具体证据演示。
5. 用 `open-source-research.md` 解释竞品与技术取舍。
6. 用 `verification-report.md` 和测试结果证明当前事实。

## 15. 完成审计清单

只有下面全部满足，导师才能说学生“可以把 muxdev 作为面试项目讲清楚”：

- [ ] 学生的贡献身份与证据一致。
- [ ] 三种长度介绍没有事实冲突。
- [ ] 能画出完整可信交付链路。
- [ ] 能解释五类 Evidence 和三种 Gate 状态。
- [ ] 能手算 strict change 的 DAG Frontier。
- [ ] 能解释两个并发不变量和两个恢复不变量。
- [ ] 能解释 Provider 输入统一到哪一层、没有统一到哪一层。
- [ ] 能用登录接口模拟完整 Evidence。
- [ ] 能主动说明四个当前边界：Provider Action、任意回滚、并行写、LangGraph。
- [ ] 能分析至少 12 个真实难点。
- [ ] 随机问题族正确率达到毕业标准。
- [ ] 能为任意核心主张找到代码、测试或产物证据。

最终要培养的不是“会背 muxdev”，而是能把一个非确定性 Agent 系统拆成明确的信任边界、状态机、事实模型、失败路径和工程取舍。这套能力可以迁移到任何 AI Agent 应用开发面试。
