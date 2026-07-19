# 核心概念速查与深入解释

[English](../en/concepts.md)

<!-- section:quick-reference -->
## 速查表

| 概念 | 一句话解释 | 主要代码入口 |
|---|---|---|
| Task / Run / Stage | 用户意图、一次执行实例和一个 DAG 单元 | `domain/run.py`、`workflows/`、`runtime/supervisor.py` |
| Workflow DAG | 决定下一步允许运行什么的依赖图 | `workflows/`、`services/orchestration.py` |
| Provider / Adapter / Harness | 外部智能体、翻译边界和生命周期契约 | `providers/adapters.py`、`providers/harness.py` |
| Routing / Certification | Provider 安全准入与有证据的能力身份 | `services/routing.py`、`providers/certification.py` |
| Worktree / 并行合并 | 把隔离修改转换成可验证 patch | `runtime/parallel_merge.py`、`runtime/worktree.py` |
| Daemon / Lease / Fencing | 具有过期所有权和旧写入拒绝机制的持久化 worker | `daemon/queue.py`、`storage/execution_queue.py` |
| Blackboard / Event / SQLite | 持久化任务状态与可回放状态转换 | `storage/blackboard.py`、`domain/state_events.py` |
| Approval / Provider Action | muxdev 策略选择与外部 CLI 交互 | `application/lifecycle.py`、`domain/provider_actions.py` |
| Context / RAG / Memory | 有界当前输入、检索来源和受治理知识 | `context/assembler.py`、`services/rag.py`、`storage/memory.py` |
| Evidence / Ledger / Attestation | 观察事实、防篡改历史和签名交付声明 | `services/evidence.py`、`storage/ledger.py`、`services/attestation.py` |
| 验证门禁 | 失败时关闭的结构化检查 | `runtime/result_validation.py`、`services/delivery_gate.py` |
| Snapshot / Recovery / Rollback | 不抹除历史地安全恢复执行 | `runtime/recovery.py`、`daemon/tasks.py` |

<!-- section:task-run-stage -->
## Task、Run 与 Stage

**定义。** Task 是用户看见的需求；Run 是执行该需求的一次持久化尝试；Stage 是 Run 中可调度的最小单元，例如 plan、implement、test、review。

**类比。** Task 像课程作业，Run 像你的一次提交，Stage 则是资料搜集、撰写、检查和提交。

**用途。** 分开这三层后，同一个 Task 可以经历重试；每个 Stage 也能拥有独立 role、Provider、policy、输出契约、Evidence 和状态。

**执行链。** 入口创建 `RunSpec` -> 队列创建 execution -> 调度器选择可运行 Stage -> adapter 返回 `StageExecutionResult` -> 生命周期 event 更新 Run projection。

**失败场景。** Test Stage 失败时，implementation 输出仍然存在。Run 进入 blocked 或可修复状态，但 Task 不会丢失。

**代码入口。** `src/muxdev/domain/run.py`、`src/muxdev/workflows/`、`src/muxdev/runtime/supervisor.py`。

**常见误解。** Task 和 Run 不是同义词。continue 或 replay 通常是在同一持久化 Run 身份与历史上创建新的 execution。

<!-- section:workflow-dag -->
## Workflow DAG

**定义。** 有向无环图把 Stage 表示为节点，把前置条件表示为有向边。muxdev 还保存有上限的条件回环元数据，但实际调度器只有原生实现。

**类比。** 大学先修课图规定学完“数据库”才能学“高级数据库”，互不依赖的选修课则可以并行学习。

**用途。** DAG 让执行顺序可检查、允许安全并行，也避免把控制流藏在 prompt 里。

**执行链。** 校验 YAML -> 对依赖做拓扑排序 -> 用条件和已完成 projection 筛选节点 -> 顺序或并行调度 -> 有界 loop 元数据可以重新打开 review/fix 区段。

**失败场景。** 环、缺失依赖或无限修复循环会被拒绝或阻塞，而不是由模型猜测。

**代码入口。** `src/muxdev/workflows/`、`src/muxdev/config/defaults/workflows.yaml`、`src/muxdev/services/orchestration.py`。

**常见误解。** Workflow 不是 Provider plugin。它是与 Provider 无关的编排数据，同一 DAG 可以使用 Mock、Codex、Qwen 或 Replay。

<!-- section:provider-adapter-harness -->
## Provider、Adapter 与 Harness

**定义。** Provider 是外部编码智能体；Adapter 把 muxdev 的类型化契约翻译给 Provider；Harness 是更大的生命周期，包括 probe、certify、execute、事件流、cancel 和 resume。

**类比。** 墙上插座提供电，旅行转换头负责插头转换，而电气安全标准规定电压和接地要求。

**用途。** 各 Provider CLI 的 flag、JSONL、prompt 传输、session、审批和错误都不同；统一类型边界避免这些差异泄漏到调度器。

**执行链。** Runtime 构造 `StageExecutionInput` -> `execute(input)` 启动受管 attempt -> parser 归一化 Provider 特有事件 -> adapter 返回 `StageExecutionResult` -> runtime 验证并持久化。

**失败场景。** CLI 退出码为 0，却只返回文字而没有 `TestResult`；进程层成功，但验证门禁会正确地让 Stage 失败。

**代码入口。** `src/muxdev/domain/stage.py`、`src/muxdev/runtime/stage_executor.py`、`src/muxdev/providers/adapters.py`、`src/muxdev/providers/harness.py`、`src/muxdev/providers/event_parsers.py`。

**常见误解。** 检测到 Provider 不等于证明它安全。Help 中可能声明某个 flag，但 certification 仍可能缺失或过期。

<!-- section:routing-certification -->
## Routing 与 Certification

**定义。** Routing 为 role 选择符合条件的 Provider；Certification 把观察到的能力绑定到 Provider 可执行文件指纹、adapter 版本和证据。

**类比。** 医院不会把手术交给“正好有空的人”，而会先检查专业、资质、时间和利益冲突。

**用途。** 成本或历史得分绝不能覆盖只读审查、sandbox、production mode 或 allowlist 等硬要求。

**执行链。** 提取有界任务特征 -> 按 policy 与认证能力过滤 -> 使用保守的质量下界 -> 记录不可变决策 -> 必要时分配不同 reviewer -> 执行前再次检查。

**失败场景。** CLI 升级后指纹改变，旧 certification 变为 stale，高风险任务会在 Provider 启动前暂停。

**代码入口。** `src/muxdev/services/routing.py`、`src/muxdev/services/routing_review.py`、`src/muxdev/providers/certification.py`。

**常见误解。** Auto routing 不是无限制的模型排行榜，而是在策略约束下使用有限本地证据作出的决定。

<!-- section:worktree-parallel-merge -->
## Worktree 与并行合并

**定义。** Worktree 是任务级 checkout；并行写入者还会得到独立 worker workspace，修改最终转换为内容寻址 patch。

**类比。** 两名同学编辑实验报告的不同副本，最后交给协调者带标签的修改单，而不是同时敲同一份文档。

**用途。** 共享可变目录会产生竞态、无声覆盖和不可复现 diff。

**执行链。** 准备基线 -> 计算 workspace hash -> 克隆 worker 目录 -> 执行 Stage -> 捕获 touched file 和 binary patch -> 验证基线 hash 与重叠 -> 确定性应用 -> 语义审查。

**失败场景。** 两个 Stage 都修改 `auth.py`，重叠检测会在任何 patch 无声胜出前阻塞合并；冲突标记也会阻塞交付。

**代码入口。** `src/muxdev/runtime/worktree.py`、`src/muxdev/runtime/parallel_merge.py`、`src/muxdev/services/semantic_merge.py`。

**常见误解。** 并行不只是线程池；隔离和确定性协调才是关键。

<!-- section:daemon-lease-fencing -->
## Daemon、Lease 与 Fencing

**定义。** Daemon 拥有持久化 job；lease 临时授予执行所有权；fencing number 让存储拒绝旧 owner 的写入。

**类比。** 图书馆自习室预约会过期，每次新预约都有更大的票号；拿着昨天票的人今天不能反锁房间。

**用途。** 进程会崩溃、机器会重启、取消存在竞态，两个 worker 也可能短暂认为自己拥有同一个 job。

**执行链。** 事务入队 -> worker 原子 claim -> heartbeat 延长 lease -> execution guard 检查 cancel/fence -> 只有当前 fence 能提交完成 -> 过期任务按风险重试或 reconciliation。

**失败场景。** Worker A 卡死，B 重新领取 job；A 醒来时携带旧 fence，写入被拒绝，因此不会完成两次。

**代码入口。** `src/muxdev/daemon/queue.py`、`src/muxdev/storage/executions.py`、`src/muxdev/domain/execution.py`。

**常见误解。** Mutex 不够。Mutex 会随进程消失，持久化 lease 和 fencing 可以跨进程存活。

<!-- section:blackboard-event-sqlite -->
## Blackboard、Event 与 SQLite

**定义。** Blackboard 是本地持久化门面；核心生命周期变化用 event 表示；便于查询的表是 SQLite 中的 projection。

**类比。** 银行既保存不可变交易流水，也维护当前余额。余额便于查询，流水负责解释余额如何产生。

**用途。** Event 支持 replay 和审计，projection 让任务状态查询更快；两者在同一事务中一起成功或一起失败。

**执行链。** 校验转换 -> 追加带 idempotency key 和 sequence 的 event -> 同事务更新 projection -> commit 后通知 -> 通过 repository/read model 查询。

**失败场景。** Projection 更新抛异常时，event 也会回滚，历史不会声称一个查询端看不到的状态变化。

**代码入口。** `src/muxdev/storage/blackboard.py`、`src/muxdev/storage/schema.py`、`src/muxdev/storage/repositories/`、`src/muxdev/domain/state_events.py`、`src/muxdev/storage/read_models/`。

**常见误解。** SQLite 不只是“缓存”，它是本地控制面的持久化数据库；删除它会破坏恢复上下文。

<!-- section:approval-provider-action -->
## Approval 与 Provider Action

**定义。** Approval 要求人类接受一个 muxdev policy subject；Provider Action 表示外部 CLI 需要交互或修复。

**类比。** 大楼管理方批准进入机房；进入后某台机器又要求操作员换纸。这两个决定来自不同权威。

**用途。** 把所有 prompt 都当审批会造成 confused-deputy 漏洞，也无法发现 subject drift。

**执行链。** Policy 计算 subject hash -> pending Approval 暂停 Run -> 记录决定 -> runtime 重新验证 subject。另一条链路中，stream parser 产生 `DetectedProviderAction` -> runtime 绑定 run/stage/provider 形成 `ProviderActionRequest` -> 记录响应 -> 用户继续。

**失败场景。** 审批后 plan 改变，旧 subject hash 不再匹配，muxdev 会重新请求审批而不是复用过期同意。

**代码入口。** `src/muxdev/application/lifecycle.py`、`src/muxdev/clients/stream.py`、`src/muxdev/domain/provider_actions.py`。

**常见误解。** muxdev 不会自动向 Provider CLI 输入“yes”；Provider 交互必须保持显式。

<!-- section:context-rag-memory -->
## Context、RAG 与 Memory

**定义。** Context 是当前 Stage 的有界输入包；RAG 检索相关 workspace 片段；Memory 保存可能跨 Run 存活的受治理知识。

**类比。** Context 是桌面材料，RAG 是图书馆检索，Memory 是整理过的笔记本。检索结果不会自动成为笔记本事实。

**用途。** 无限 prompt 会超出 token 预算，陈旧事实会误导智能体，不可信笔记还可能覆盖当前 blocker。

**执行链。** 收集 task 与 P0 blocker -> 对来源评分 -> 有必要时检索 citation -> 加入 active 且 evidence-grounded 的 Memory -> 执行确定性预算 -> 写 context packet/hash -> 绑定到 Stage 输入。

**失败场景。** 两条 Memory 相互矛盾时，较低信任项会被 quarantine，而不是静默注入。

**代码入口。** `src/muxdev/context/assembler.py`、`src/muxdev/context/budget.py`、`src/muxdev/services/rag.py`、`src/muxdev/storage/memory.py`。

**常见误解。** RAG 与 Memory 不相同。RAG 是“现在检索”，Memory 需要生命周期和信任治理。

<!-- section:evidence-ledger-attestation -->
## Evidence、Ledger 与 Attestation

**定义。** Evidence 记录支撑结论的观察；Ledger 对重要记录做哈希链；Attestation 对一次完成交付的有界声明签名。

**类比。** Evidence 是实验测量，Ledger 是编号实验记录本，Attestation 是引用这些测量的签名结论。

**用途。** 自然语言信心不是证明。使用者需要知道运行了哪些测试、哪些 artifact 被哈希、身份或历史是否不完整。

**执行链。** Stage 产生 artifact/event -> Evidence manifest 评估覆盖 -> Ledger 追加 canonical hash -> 完成时收集 allowlist 事实 -> 项目 key 签名 payload -> 可选 `.muxattest` bundle 离线验证。

**失败场景。** 签名后 artifact 被修改，hash 不匹配，bundle 验证失败；缺失 key 时产物会明确标记 unsigned，而不是伪造身份。

**代码入口。** `src/muxdev/services/evidence.py`、`src/muxdev/storage/ledger.py`、`src/muxdev/services/attestation.py`、`src/muxdev/services/attestation_bundle.py`。

**常见误解。** 有效签名只能证明 payload 完整性和 key 持有关系，不能证明其中每条结论客观正确或得到组织背书。

<!-- section:validation-gates -->
## 验证门禁

**定义。** Gate 是 Stage 或交付前必须通过的确定性检查，包括 schema、test、review、policy、budget、isolation、Evidence 和 artifact 检查。

**类比。** 编译器接受语法不代表程序正确；类型检查、测试、review 和发布检查回答的是不同问题。

**用途。** Provider 写“测试全部通过”既含糊，也可能是幻觉。

**执行链。** 解析 JSON object -> 校验 `TestResult` 或 `ReviewResult` -> 对照 boolean、exit code、blocker -> 记录契约 Evidence -> 评估 delivery standard -> block、repair 或 finalize。

**失败场景。** `passed: true` 但 `exit_code: 1` 自相矛盾，会变成失败结果；缺少 Review JSON 会产生 high-severity blocker。

**代码入口。** `src/muxdev/runtime/result_validation.py`、`src/muxdev/services/delivery_gate.py`、`src/muxdev/storage/contracts.py`。

**常见误解。** 退出码为 0 只表示 Provider 进程正常结束，不能清除交付门禁。

<!-- section:recovery-rollback -->
## Snapshot、Recovery 与 Rollback

**定义。** Snapshot 捕获已知执行边界；Recovery 协调持久化所有权与未完成状态；Rollback 恢复选定文件系统快照，同时保留审计历史。

**类比。** 版本化存档点让游戏崩溃后重新打开；活动日志仍会记录崩溃与恢复发生过。

**用途。** 盲目重试可能重复副作用，破坏性 reset 又会抹掉判断安全性所需的证据。

**执行链。** 检测 blocked/expired execution -> 分类 attempt 安全性 -> 对不透明副作用要求 reconciliation -> 带 reason 重置可恢复 Stage projection -> 恢复 snapshot 或 resume -> 获取新 fenced lease -> 继续。

**失败场景。** Lease 丢失时，不透明 Provider 可能已经对外写入；muxdev 拒绝自动重试，直到人工协调副作用。

**代码入口。** `src/muxdev/runtime/recovery.py`、`src/muxdev/daemon/tasks.py`、`src/muxdev/storage/executions.py`。

**常见误解。** Rollback 不是 `git reset --hard`，也不会重写数据库；它是保留历史的受控恢复事件。
