# muxdev 深度面试答辩：从 Coding Agent 到可信交付控制面

> 适用代码：当前 `release/v3` 分支及本轮优化后的实现。本文故意区分“已经实现”“有限实现”和“尚未实现”，避免把设计设想包装成项目事实。

## 先建立零基础上下文

Codex、Claude Code、Cursor 这类产品的核心能力是“执行开发工作”：理解需求、阅读代码、修改文件、运行命令并和人对话。muxdev 不再造一个基础模型，也不试图替代它们。muxdev 位于这些 Coding Agent 的外层，负责四件事：

1. 用固定工作流组织计划、实现、测试、普通审查、安全审查和人工批准。
2. 把 Codex、Claude Code、Qwen 等不同 CLI 适配到同一运行时输入和结果模型。
3. 不相信 Agent 自己说“测试通过”，而是由 Runtime 重放测试、计算摘要、绑定审查对象并执行确定性门禁。
4. 将一次交付保存成可验证的 Evidence Report，只有门禁为 `PASS` 才把变更从隔离 Worktree 回写到用户工作区。

因此最准确的一句话不是“muxdev 是另一个 Claude Code”，而是：

> muxdev 是一个本地优先、跨 Coding Agent 的可信交付控制面；Coding Agent 负责提出和执行变更，muxdev 负责证明这份变更能否被接受。

这里有三个必须先理解的术语：

- **Provider**：真正执行某个阶段的 Coding Agent，例如 Codex CLI 或 Claude Code CLI。
- **Subject**：本次准备交付的精确代码状态。当前实现用 Worktree 的规范化 Git Diff 计算 SHA-256 摘要。
- **Evidence**：能够被门禁消费的结构化事实，例如 Diff 的摘要、Runtime 实际观察到的退出码、Review 的目标摘要和 Reviewer 身份。聊天记录可以帮助人理解过程，但不自动等于 Evidence。

---

## Q1：如果已经有 Codex、Claude Code、Cursor，为什么还需要 muxdev？

### 结论

不是所有人都需要 muxdev。如果是个人完成一次低风险修改，直接使用 Codex、Claude Code 或 Cursor 更快。muxdev 解决的是另一个问题：当一次 Agent 变更需要跨 Provider、可恢复、可复验、可审计，并且必须满足统一交付政策时，不能只依赖某个 Agent 的对话体验。

可以把两者类比为：

- Coding Agent 像一名能力很强的开发者。
- muxdev 像开发团队外层的任务编排、CI 门禁、审查规则和交付凭证系统。

开发者会写代码，但“开发者说已经测过”不能替代 CI 的实际退出码；同理，Agent 的自然语言总结不能替代 Runtime 事实。

### muxdev 额外提供的价值

| 维度 | 单独使用 Coding Agent | muxdev 外层控制面 |
|---|---|---|
| 执行能力 | 强，直接修改代码、调用工具 | 复用现有 Agent，不重复造模型 |
| Provider 选择 | 通常绑定当前产品 | 同一个工作流可路由到不同 CLI，并给 Review 角色选择异构 Provider |
| 交付标准 | 主要靠提示词、Rules、Hooks 或产品内设置 | 冻结 `EvidencePolicy`，由确定性代码判定 `PASS / BLOCKED / WAITING_HUMAN` |
| 测试可信度 | Agent 可以运行测试并留下 Transcript | Runtime 按结构化 `argv` 重放测试，以实际退出码为准 |
| 审查可信度 | 可以让 Agent 自审或调用子 Agent | Review 绑定最终 Subject 摘要，并检查 Reviewer 与 Executor 是否不同 |
| 恢复 | 各产品有自己的 Session/Checkpoint | muxdev 持久化 Run、Stage、Interaction 和事件链；已完成阶段不会重复执行 |
| 供应链证据 | 通常是会话、Diff、Checkpoint | 统一 Evidence v3 报告，支持哈希事件链和可选 DSSE 签名 |
| 历史上下文 | CLAUDE.md、Rules、Memory、会话历史 | 只从仍能验证为 `PASS` 的历史交付中检索，且带 Run ID 和 Subject 摘要 |

### 不应该夸大的地方

Claude Code 已经支持 Session Transcript、Hooks、Memory、Checkpoint 和 Worktree；Cursor 也有 Rules、Memory 和 Checkpoint。通过大量工程配置，完全可以在某一个产品里搭出类似流程。muxdev 的价值不是“竞品绝对做不到”，而是把这些交付约束做成一个跨 Provider、默认统一、可独立验证的产品边界。

官方资料也印证了这个边界：Claude Code 明确把 `CLAUDE.md` 和 Auto Memory 描述为注入上下文的指导信息，而不是强制配置；需要确定性行为时应使用 Hooks。Codex app-server 则把 Turn、Event 和 Approval 分成独立协议资源。muxdev 借鉴这些成熟模式，但把目标收窄到交付门禁。[Claude Code Memory](https://code.claude.com/docs/en/memory)、[Claude Code Hooks](https://code.claude.com/docs/en/hooks-guide)、[Codex app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)

---

## Q2：Claude Code 也会产出记录，muxdev 所谓的“证据”是什么？

### Transcript 与 Evidence 不是同一种数据

Claude Code Session 会保存提示词、工具调用、工具结果和回复，这是一条非常有价值的**过程记录**。但过程记录里混合了意图、推理、建议、命令输出和最终结论，下游程序如果想判断“能不能交付”，仍需自己解释它。

muxdev Evidence 是面向门禁的**类型化事实**。当前只有五类：

1. `ArtifactEvidence`：文件路径、媒体类型、字节数和内容摘要。
2. `CheckEvidence`：Runtime 实际执行的参数数组、工作目录摘要、实际退出码、Agent 声明的退出码、耗时和 stdout/stderr 摘要。
3. `ReviewEvidence`：Reviewer、Executor、审查目标摘要、是否独立、结构化 Finding 和剩余风险。
4. `InteractionEvidence`：人工批准、拒绝或反馈以及当前决策状态。
5. `RuntimeEvidence`：运行时观察，例如上下文准备完成、超时、契约错误、只读越权写入或运行健康状态。

核心模型位于 `src/muxdev/models/evidence.py`。以测试证据为例：

```python
class CheckEvidence(EvidenceRecord):
    kind: Literal["check"] = "check"
    argv: list[str]
    cwd: str = "."
    cwd_digest: str
    exit_code: int                 # Runtime 实际观察值
    declared_exit_code: int | None # Agent 声明值
    declared_status: Literal["passed", "failed", "skipped", "unavailable"] | None
    duration_ms: int = 0
    stdout_digest: str | None = None
    stderr_digest: str | None = None
    reproducible: bool = True

    @property
    def passed(self) -> bool:
        return self.exit_code == 0
```

这里最重要的是 `exit_code` 和 `declared_exit_code` 分开。Agent 可以声称测试通过，但 `RunEngine._run_check()` 会重新执行 `argv`。若 Agent 声明为 0，而实际退出码是 3，`EvidencePolicy` 会产生硬阻塞，不允许分数覆盖。

### 什么叫“证据”

一条数据至少满足以下条件，才适合被 muxdev 当成可采信 Evidence：

- 有稳定 Schema，而不是需要下游猜测的一段自然语言。
- 有 Producer，能区分 Agent 声明和 Runtime 观察。
- 绑定 Run、Stage、Requirement 和 Subject。
- 能检查完整性，例如内容摘要、事件链或签名。
- 能给出失败原因和修复建议。
- 能由冻结的 Policy 重新计算相同 Gate 结论。

因此 Transcript 回答“Agent 做过什么、说过什么”，Evidence 回答“哪些可验证事实足以支持或阻止交付”。两者互补，不是互相取代。

---

## Q3：用 muxdev 开发登录接口，会保存哪些实际证据？

假设任务是：

> 新增 `POST /api/login`；校验用户名和 Argon2id 密码哈希；成功返回短期访问令牌；失败统一返回 401；增加暴力破解限速；补齐正常登录、错误密码和限速测试。

严格模式会产生如下证据和辅助工件。

### 1. 冻结的运行输入

- `run_id`、任务文本、Workflow=`change`、Profile=`strict`。
- 当时的 Workflow Definition 和 EvidencePolicy 全量内容及 `policy_hash`。
- 路由决策：主 Executor、普通 Reviewer、安全 Reviewer 的选择理由、候选能力、历史质量下界、成本和延迟。
- Worktree 路径和最大成本。

Policy 在 Run 创建时写入 Metadata，恢复时读取冻结副本。即使用户在暂停期间修改项目的 `evidence-policy.yaml`，旧 Run 仍按原 Policy 继续，避免“审批前后规则被偷偷换掉”。对应代码是 `RunEngine.run()` 和 `RunEngine._policy()`。

### 2. 计划阶段

- Provider 输出的 `PlanResult`：设计决策、验收标准、步骤、风险、假设和待确认问题。
- 原始阶段输出文件，例如 `artifacts/plan/plan.json`。
- `ArtifactEvidence`：该文件的摘要、大小、路径和 Producer。
- 该阶段实际收到的 `context-pack.json`，里面有上下文摘要和 Manifest。

### 3. 实现阶段

- `ChangeResult`：改了哪些路径、建议如何验证、还剩哪些风险。
- Worktree 中的真实文件变更。
- 最终 `diff.patch` 及摘要；最终 Subject 是对规范化 Diff 再计算的 SHA-256。
- Provider 协议元数据，例如 `codex.jsonl`、事件数、Session ID。注意这些是诊断信息，不直接决定 Gate。

### 4. 测试阶段

Agent 返回结构化 `TestResult`，例如建议运行：

```json
{
  "checks": [
    {
      "id": "login-success",
      "argv": ["python", "-m", "pytest", "tests/test_auth.py::test_login_success", "-q"],
      "status": "passed",
      "exit_code": 0,
      "summary": "正确密码返回 access token"
    }
  ]
}
```

随后 Runtime 再执行同一个参数数组，保存：

- 实际 `exit_code`。
- 实际耗时。
- stdout/stderr 的摘要值和末尾摘要。
- Agent 的声明值。
- 声明是否与观察一致。
- 是否具备可重放的非空 `argv`。
- Check 执行前后 Subject 是否保持不变；退出码为 0 但命令偷偷改源码同样会被标记为完整性失败。

若 Agent 写成 `passed=true` 但命令实际失败，或测试命令返回 0 却修改了交付 Subject，`integrity_valid=false`，交付被阻塞。

### 5. 普通审查和安全审查

严格模式在测试后对同一个不可变 Subject 并行执行两个只读 Worker：

- 普通 Review：正确性、可维护性、错误处理和测试覆盖。
- Security Review：密码处理、用户枚举、限速、令牌生命周期、日志泄漏等。

每条 `ReviewEvidence` 保存：

- `target_digest`：Reviewer 声称审查的精确 Subject。
- `reviewer` 和 `executor`。
- `independent`：两者是否不同。
- Finding 的严重级别、文件、行号、说明和修复建议。
- 剩余风险。

若 Reviewer 审查的是旧 Diff，或 Reviewer 与 Executor 相同而 Policy 要求独立审查，Gate 会阻塞。

### 6. 人工批准

- `interaction.requested` 事件。
- Pending Interaction 行。
- 人的 Approved/Rejected 响应。
- 最新状态对应的 `InteractionEvidence`。

人工批准不能覆盖失败测试或自审。它只是 Policy 中的一个独立 Requirement。

### 7. 运行时和完整性

- 每个 Evidence Record 都进入 append-only 事件流。
- 每个事件包含序号、前一事件摘要和本事件摘要。
- `evidence-report.json` 保存事件链头、记录集合摘要和 Gate 重算结果。
- 显式执行 `muxdev evidence export` 时，可生成 DSSE Envelope，把签名绑定到 Evidence Report 摘要。

### 8. 最终动作

- `PASS`：把 Worktree 的变更复制回主工作区，并记录 `workspace.applied`。
- `WAITING_HUMAN`：保留 Run 和 Worktree，等待批准后 Resume。
- `BLOCKED`：不回写主工作区，保留证据和隔离现场供排查。

---

## Q4：模拟一份实际证据数据

完整、可通过 Pydantic Schema 校验的样例见 `release-artifacts/login-evidence-example.json`，生成器见 `scripts/generate_login_evidence_example.py`。样例被明确标记为 `simulated=true`，不会伪装成真实运行结果。

下面截取测试、审查和门禁的关键部分：

```json
{
  "contract_version": "muxdev.evidence.v3",
  "run_id": "run_login_demo_001",
  "subject": {
    "name": "login-api-change",
    "digest": "sha256:...",
    "simulated": true
  },
  "records": [
    {
      "record_id": "ev_check_login_success",
      "kind": "check",
      "requirement_id": "deterministic_check",
      "producer": "muxdev.runtime.command",
      "argv": [
        "python",
        "-m",
        "pytest",
        "tests/test_auth.py::test_login_success",
        "-q"
      ],
      "exit_code": 0,
      "declared_exit_code": 0,
      "declared_status": "passed",
      "duration_ms": 842,
      "reproducible": true,
      "integrity_valid": true
    },
    {
      "record_id": "ev_review_login_001",
      "kind": "review",
      "requirement_id": "independent_review",
      "target_digest": "sha256:...",
      "reviewer": "claude-code",
      "executor": "codex",
      "independent": true,
      "findings": []
    },
    {
      "record_id": "ev_security_login_001",
      "kind": "review",
      "requirement_id": "security_review",
      "target_digest": "sha256:...",
      "reviewer": "qwen",
      "executor": "codex",
      "independent": true,
      "findings": []
    }
  ],
  "decision": {
    "status": "PASS",
    "blockers": [],
    "scorecard": {
      "completeness": {"numerator": 7, "denominator": 7, "percent": 100.0},
      "reproducibility": {"numerator": 2, "denominator": 2, "percent": 100.0},
      "integrity": {"numerator": 8, "denominator": 8, "percent": 100.0},
      "independence": {"numerator": 2, "denominator": 2, "percent": 100.0},
      "overall": 100.0
    }
  }
}
```

这份数据真正有用的地方不是字段多，而是能做三件事：

1. 重新执行 Gate，验证原结论没有被手改。
2. 重新计算文件、记录集合和事件链摘要，发现篡改。
3. 精确解释哪个 Requirement 为什么失败以及如何修复。

---

## Q5：与“长期记忆文件 + Claude Code”相比有什么好处？

### 长期记忆解决“下次该知道什么”

`CLAUDE.md`、Auto Memory 或 Cursor Rules 很适合保存：

- 项目使用什么包管理器。
- 编码风格和目录约定。
- 测试命令。
- 上次调试得到的经验。

它们的主要消费者是模型，目标是提高下一次生成质量。

### Evidence 解决“这一次凭什么可以交付”

Evidence 保存的是：

- 最终改动的精确摘要是什么。
- 哪个命令在什么上下文中实际运行。
- 实际退出码是多少。
- 谁审查了哪个摘要。
- 哪条 Policy Requirement 被满足或失败。
- 记录是否被篡改。

它的主要消费者既包括人，也包括确定性 Gate 和外部验证器。

### 本轮优化后的 Memory 也和普通长期记忆不同

muxdev 不允许 Agent 随手把一句自然语言直接晋升为可信长期记忆。`retrieve_verified_memory()` 只读取满足全部条件的历史 Run：

```python
for run in store.list_runs(status="completed", limit=100):
    verification = verify_evidence_report(report_path, store=store)
    if not verification.get("valid") or verification.get("gate_status") != "PASS":
        continue
    # 只有仍可验证的 PASS 报告才进入检索候选
```

然后用本地 BM25 对任务和结构化阶段摘要排序，把 Run ID、Subject 摘要和简短事实放入 Context Pack。这样解决了普通 Memory 常见的三个问题：

- **Memory Poisoning**：失败运行或被篡改报告不会进入检索。
- **缺少出处**：每条命中带 `run_id` 和 `subject_digest`。
- **上下文膨胀**：只取 Top-K，且受总字符预算限制。

因此不是“Evidence 比 Memory 更高级”，而是二者职责不同：Memory 提升执行，Evidence 证明交付；muxdev 只允许经过 Evidence 治理的历史事实参与长期检索。

---

## Q6：三个理解是否正确？

### ① 每次操作记录都统一规范完整，不依赖提示词？

**部分正确，需要加边界。**

- 对 muxdev 自己管理的阶段、测试重放、Evidence、Interaction 和 Gate，Schema 与采集逻辑由代码决定，不依赖 Agent 是否记得写某句提示词。
- “完整”不是口号，而是由 Policy Requirement 检查。例如缺少独立审查时，报告仍然完整生成，但结论是 `BLOCKED`，并明确标记 `independent_review=missing/failed`。
- muxdev 当前不会完整捕获 Provider 内部所有 Token、每次工具调用和隐式推理。Headless CLI 暴露什么事件，适配器才能规范化什么；这些协议元数据也不会自动升级为可信 Gate Evidence。

所以准确说法是：**muxdev 对自己承诺的交付事实执行统一 Schema 和完整性门禁，不声称记录 Provider 的全部内部行为。**

### ② 会根据证据自动判断能否通过，不通过会处理？

**正确。**

`evaluate_gate()` 对冻结 Policy 的每个 Requirement 找匹配 Evidence，依次检查：

- 是否缺失。
- 测试声明与 Runtime 退出码是否矛盾。
- 完整性是否有效。
- 是否有失败 Check。
- Runtime 是否失败或 Pending。
- Review 是否绑定最终 Subject。
- 是否独立审查。
- 是否存在未解决的 High Finding。
- 人工批准是否 Pending 或 Rejected。

结论只有三个：

- `PASS`：应用变更。
- `WAITING_HUMAN`：暂停，等待 Interaction。
- `BLOCKED`：不应用，输出 Blocker、Record ID 和 Remediation。

Change Workflow 对普通/安全 Review 的 High Finding 支持最多两轮 `fix → test → fan-out review`。两轮仍失败、成本超限、超时或输出不符合 Schema，就停止并阻塞，不允许无限对话。

### ③ 可以通过证据链精准退回任意节点？

**当前不正确。**

当前实现提供的是：

- 已完成 Stage 的持久化检查点。
- 暂停后 Resume，跳过已完成 Stage。
- 每个 Run 的独立 Worktree，失败时主工作区未被污染。
- 事件链能定位发生过什么。

但它没有提供一个公开的 `rollback <run-id> --stage <id>`，也没有为每个 Stage 保存可直接恢复的 Git Tree。Evidence Chain 是审计链，不等于状态快照。若面试中说“可精准回退任意节点”，会超过当前代码事实。

合理的后续实现应在每个写阶段后保存 Git Tree OID/Commit OID，把 Stage Checkpoint 与 Tree 绑定；回退时创建新的分支或 Worktree，而不是改写旧事件链。LangGraph 的 Time Travel 也区分“回放旧 Checkpoint”与“修改原历史”；Claude Code Checkpoint 同样强调它不是 Git 永久历史。[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Claude Code Checkpointing](https://code.claude.com/docs/en/checkpointing)

---

## Q7：开发用户登录接口时，比 Claude Code 更多地帮在哪里？

一次性按时间线说明：

1. **运行前**：muxdev 冻结严格 Policy，选择 Codex 做实现、Claude Code 做普通 Review、Qwen 做安全 Review，并创建隔离 Worktree。
2. **计划时**：保存结构化 Plan 和验收标准，不只是聊天中的一段计划。
3. **实现时**：把前序 Plan 的结构化事实、Repo Map 和经过验证的历史登录交付压缩进 Context Pack；Provider 仍负责写代码。
4. **测试时**：Agent 可以建议测试命令，但 muxdev Runtime 按参数数组重放，捕获实际退出码和输出摘要。
5. **审查时**：普通 Review 与 Security Review 在同一个 Subject 摘要上并行执行；Reviewer 必须与 Executor 身份不同。
6. **并发一致性**：两个 Worker 都被声明为只读。任一 Worker 修改 Worktree，运行时比较前后 Subject，记录失败并阻止回写。
7. **失败修复**：High Finding 触发有界修复循环；每次 Fix 后重新 Test，并对新 Subject 重新执行普通和安全 Review。
8. **人工控制**：严格模式的批准是独立持久化资源，批准不能覆盖失败测试。
9. **交付时**：只有 Gate `PASS` 才应用 Diff；否则主目录保持不变。
10. **交付后**：保存可重算报告、事件链和可选 DSSE；未来相似任务只检索仍然可验证的 PASS 历史。

Claude Code 自身可以写登录接口、运行测试、开子 Agent、使用 Hooks、Memory、Checkpoint 和 Worktree。muxdev 多出的不是“代码生成能力”，而是跨 Provider 的统一 Policy、Runtime 重放、Subject 绑定、身份独立性、Fail-closed Gate 和 Evidence 产物。

如果团队只用 Claude Code，也可以用 Hooks + Agent SDK + Session Store 自建这些能力；但那已经是在实现一个类似 muxdev 的控制面，而不再只是写一条提示词。

---

## Q8：除了交付规范，技术亮点有哪些？

### 1. Provider Anti-Corruption Layer

Runtime 永远只认识 `StageExecutionInput → StageExecutionResult`。Prompt 走 stdin 还是 argv、输出是 Codex JSONL 还是 Claude stream-json，由 `providers/protocols.py` 处理。这样 Provider 协议变化不会渗透进 Gate。

### 2. 自研小型 Supervisor DAG

Workflow 是显式 DAG；启动前检查缺失依赖和环，执行时计算确定性 Frontier。它不是 LLM 自由决定下一位发言者，而是可验证的固定控制流。

### 3. 受约束 Fan-out/Fan-in

只对同一 Subject 上的独立只读阶段并行，例如普通 Review 和 Security Review。写阶段保持串行单写者；结果按 Stage 定义顺序归并，事件记录 Fan-out 的 Subject 与 Merge Order。

### 4. Runtime 与 Provider 的信任边界

Provider 输出被当作 Claim。Diff、退出码、文件摘要、身份和 Interaction 由 Runtime 产生。禁止 Provider 返回 `delivery_decision`、`confidence` 或 `evidence` 来控制 Gate；Runtime 重放 Check 时也验证 Subject 未被命令副作用修改。

### 5. Durable Resume 与不安全重放保护

Run、Job、Stage 和 Interaction 落 SQLite。Resume 跳过 Completed Stage；若崩溃时存在非 Mock 的 Opaque Provider Stage，当前实现拒绝自动重放，要求协调，避免重复外部副作用。

### 6. Worktree 隔离与写集验证

每个 Run 使用 Git Worktree，非 Git 环境退化为隔离 Copy + 本地 Git Baseline。只读阶段执行前后比较 Subject，防止 Reviewer 越权修改交付物。

### 7. 可信历史驱动路由

ProviderRouter 只把 `Gate=PASS && integrity_valid && independent_valid` 的 Outcome 纳入 Beta 下界统计；Agent 自报 Confidence 不进入历史质量。再结合 Cost P90 与 Latency P90 计算可解释分数。

### 8. Evidence-grounded RAG 与 Context Engineering

当前没有引入向量数据库。它把前序结构化输出、AST Repo Map 和验证通过的历史交付组成最大 12,000 字符的 Context Pack，记录截断区段、来源 Run ID 和 Context Digest。这是一个可替换 Retriever 的轻量 RAG，而不是无治理的聊天记忆。

### 9. 哈希事件链和 DSSE

Evidence Record 进入带 `previous_hash` 的 Append-only Event Stream；Evidence Report 保存 Chain Head 和 Records Hash。可选 DSSE Envelope 只绑定报告摘要，不复制一套业务事实。

### 为什么不引入 LangGraph？

LangGraph 的 Checkpointer、Interrupt、Pending Writes、Time Travel 和 `Send` 动态 Worker 都很成熟，适合运行时拓扑动态、长生命周期、多种状态通道的系统。[LangGraph Workflows](https://docs.langchain.com/oss/python/langgraph/workflows-agents)、[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

muxdev 只有四个固定 Workflow、三个 Profile、一个两轮 Repair Loop 和一个只读 Review Fan-out。引入 LangGraph 会同时保留 muxdev Evidence Store 和 LangGraph Checkpointer，形成两套恢复事实源。当前选择是借鉴其语义，自己实现约 4 个固定不变量。面试亮点不是“用了热门库”，而是能说明什么时候不应该用。

---

## Q9：统一 Coding Agent CLI Adapter 遇到了什么问题？现在如何调度？

### 遇到的问题

#### 1. Prompt 传输不同

- Codex Headless 可以在 `codex exec` 时从 stdin 读取 Prompt。
- Claude Code 常见写法是 `claude -p "<prompt>" --output-format stream-json`，Prompt 在参数里。
- Qwen 配置需要在 `-p` 后明确跟 Prompt。

旧通用适配器有一个真实缺陷：当命令模板没有 `{prompt}` 时，即使末尾已经有 `-p`，它也会把 Prompt 送到 stdin，产生“有 `-p` 但没有参数”的错误。

现在由 `build_cli_invocation()` 强制校验：

```python
def build_cli_invocation(template, prompt, *, transport):
    placeholders = sum(item.count("{prompt}") for item in argv_template)
    if transport == "argument":
        if placeholders != 1:
            raise ValueError("argument prompt transport requires exactly one {prompt} placeholder")
        return CliInvocation(argv=..., stdin=None, transport=transport)
    if transport == "stdin":
        if placeholders:
            raise ValueError("stdin prompt transport cannot also contain a {prompt} placeholder")
        return CliInvocation(argv=argv_template, stdin=prompt, transport=transport)
```

这保证 Prompt 恰好只有一个来源，避免同时放 argv 和 stdin，也避免两边都没放。

#### 2. JSONL 事件结构不同

Codex 的最终消息常在 `item.completed.item.type=agent_message`；Claude 的文本可能出现在 `assistant.message.content[]`，最终结果也可能是 `type=result`。以前用正则搜索任意 `"text"`，可能把工具输出中的文本误当成最终回答。

现在按 Provider 协议解析，规范化为：

- `content`
- `protocol`
- `event_count`
- `session_id`

这些字段进入 Stage Result 供诊断，但不会直接成为 Gate 结论。

#### 3. 能力不等于安装

仅仅能找到二进制不代表支持 Headless、JSON、Approval、Resume，也不代表已登录。Registry 先静态 Probe，非 Mock Provider 还需要 Certification。Router 对能力和认证 Fail-closed。

#### 4. Provider Approval 不统一

Codex app-server 有双向 JSON-RPC Approval；Claude `-p` 非交互模式下某些 Permission Hook 不触发；不同 CLI 的“等待输入”语义不同。当前 Headless 执行采用不交互策略，Provider Action 的统一暂停/恢复尚未完成，见 Q17。

#### 5. Read-only 只是声明不够

即使 `StageExecutionInput.capabilities.read_only=true`，外部 CLI 仍可能写文件。因此 Runtime 还要比较执行前后的 Subject；仅靠 Prompt 或 Capability Flag 不构成安全边界。

### 当前调度链路

```text
TaskService.create
  → RunEngine.run
  → ProviderRouter.route
  → execution_waves(workflow)
  → _stage_provider(role, route, overrides)
  → get_runtime_provider(provider)
  → HeadlessCliProviderAdapter.execute(StageExecutionInput)
  → build_cli_invocation + subprocess
  → parse_cli_output
  → StageExecutionResult
  → Runtime 验证、Evidence、Gate
```

角色选择规则是：

1. 如果调用方提供 `role_providers` 覆盖，优先使用。
2. `review/secure` 优先使用 Router 选出的异构 Reviewer。
3. 其他角色使用 Main Provider。

---

## Q10：每个 CLI 的输入统一了吗？命令如何转发？

### 统一的是语义输入，不是强行统一命令行语法

所有 Provider 都接收同一个不可变对象：

```python
@dataclass(frozen=True)
class StageExecutionInput:
    run_id: str
    stage_id: str
    role: str | None
    task: str
    worktree: Path
    context: Mapping[str, object]
    capabilities: Mapping[str, object]
    provider: str
    policy: Mapping[str, object]
    skills: tuple[Mapping[str, object], ...] = ()
    attempt: int = 1
```

其中：

- `task` 是用户目标。
- `context` 包含 Subject、Context Pack 和 Context Manifest。
- `capabilities` 表明阶段是只读、可写或可执行 Shell。
- `policy` 包含输出 Schema 和 Timeout。
- `skills` 是阶段角色指导，不能扩大权限或修改 Gate。

### 同一输入转给 Codex

配置：

```yaml
command:
  - codex
  - --ask-for-approval
  - never
  - exec
  - --json
  - --skip-git-repo-check
  - --sandbox
  - workspace-write
prompt_transport: stdin
```

实际调用概念上是：

```text
argv  = [codex, --ask-for-approval, never, exec, --json, ...]
stdin = "Execute muxdev stage 'implement' ... + Output Schema + Context Pack + Skill"
cwd   = <run-worktree>
env   = MUXDEV_RUN_ID=..., MUXDEV_STAGE_ID=implement
```

### 同一输入转给 Claude Code

配置：

```yaml
command: [claude, -p, "{prompt}", --output-format, stream-json, --verbose]
prompt_transport: argument
```

实际调用概念上是：

```text
argv  = [claude, -p, "<完整 Prompt>", --output-format, stream-json, --verbose]
stdin = null
cwd   = <run-worktree>
```

### 同一输入转给 Qwen

```yaml
command: [qwen, --bare, --sandbox, --approval-mode, auto,
          --output-format, stream-json, --max-tool-calls, "40", -p, "{prompt}"]
prompt_transport: argument
```

因此 Adapter 模式的本质是：**Domain Contract 统一，边缘协议保留差异，由 Anti-Corruption Layer 转换。** 如果把所有 CLI 强行拼成同一种 argv，Provider 一升级参数就会污染核心 Runtime。

---

## Q11：自研 Supervisor DAG；常见多 Agent 协作模式有哪些？

### 常见模式

1. **Prompt Chaining**：A 的结构化输出传给 B，适合步骤固定的任务。
2. **Routing**：先分类，再选择专业 Agent/模型/工具。
3. **Parallel Sectioning**：把互不依赖的子任务并行，再汇总。
4. **Parallel Voting**：多个 Agent 做同一任务，以投票或 Judge 提高置信度。
5. **Orchestrator-Workers**：编排者拆解任务、分配 Worker、收集并合成结果。
6. **Evaluator-Optimizer / Reflection**：生成者产出，评估者给反馈，生成者有界修复。
7. **Group Chat / Selector**：多个 Agent 共享消息线程，由 Manager 选择下一位发言者。
8. **Swarm / Handoff**：当前 Agent 根据能力把控制权移交给另一个 Agent。
9. **Blackboard**：所有 Agent 读写共享状态，由规则或 Coordinator 推进。
10. **Hierarchical Teams**：Supervisor 管理子 Supervisor，适合大规模组织。

Anthropic 将 Workflow 定义为预设代码路径、Agent 定义为模型动态决定过程，并建议优先使用最简单、可组合的模式；LangGraph 展示了 Parallelization、Orchestrator-Worker 和动态 `Send`；AutoGen 展示了 Group Chat 与 Swarm。[Anthropic Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)、[LangGraph Workflows](https://docs.langchain.com/oss/python/langgraph/workflows-agents)、[AutoGen Group Chat](https://microsoft.github.io/autogen/0.5.5/user-guide/core-user-guide/design-patterns/group-chat.html)、[AutoGen Swarm](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/swarm.html)

### muxdev 选择的组合

muxdev 不是自由对话的 Agent 社会，而是四个固定 Workflow 上的：

- Orchestrator-Workers：RunEngine 是确定性 Supervisor，Stage Provider 是 Worker。
- Prompt Chaining：Plan → Implement → Test → Review。
- Routing：按能力、认证、历史质量、成本和延迟选择 Provider。
- Parallel Sectioning：普通 Review 与 Security Review 并行。
- Evaluator-Optimizer：Review High Finding → Fix → Test → Review，最多两轮。
- Human-in-the-loop：严格模式显式暂停和恢复。

这里“自研 Supervisor”不意味着又调用一个 LLM 来当领导。它是代码实现的状态机/DAG 调度器，优势是行为可预测、状态可落库、Gate 可重算。

---

## Q12：详细解释 Orchestrator-Workers，难点在哪？

### 模式定义

Orchestrator 负责：

1. 理解或接收任务分解。
2. 建立 Worker 依赖关系。
3. 给每个 Worker 最小必要上下文和权限。
4. 调度串行或并行执行。
5. 处理失败、重试、超时和人工中断。
6. 汇总结构化结果并决定下一步。

Worker 负责一个边界清楚的子任务，例如 Implement、Test、Review 或 Security Review。

### 真正的难点

#### 难点 1：拆分是否真的独立

如果两个 Worker 都修改同一个文件，并行只会把时间问题变成冲突问题。muxdev 只并行同一 Subject 上的只读审查；写阶段单写者串行。

#### 难点 2：上下文边界

把全部 Transcript 发给所有 Worker 会浪费 Token，也会传播噪声。muxdev 只传前序结构化结果、Repo Map 和 Top-K Verified Memory，并记录 Context Digest。

#### 难点 3：结果协议

自然语言很难稳定 Fan-in。muxdev 为 Plan、Change、Test 和 Review 定义 Pydantic Schema；无效输出 Fail-closed。

#### 难点 4：共享状态一致性

并行 Worker 必须看到同一个 Subject。Supervisor 在 Fan-out 前冻结摘要，所有 Worker Input 使用相同值，Fan-in 时再次检查 Worktree 摘要。

#### 难点 5：失败语义

“一个 Worker 失败，另一个成功”该如何处理？muxdev 会保存成功 Worker 的产物，但整个 Wave 标记失败；Gate 不因部分成功而放行。

#### 难点 6：副作用与重放

LLM/API 调用不是天然幂等。muxdev 对 Completed Stage 不重跑；对崩溃中的 Opaque Provider Stage 不擅自重放。LangGraph 官方也特别提醒 Interrupt Resume 会从 Node 开头重跑，前置副作用必须幂等。[LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

#### 难点 7：终止条件

没有最大轮数、成本和超时的协作可能无限消耗。muxdev 用两轮 Repair、Profile Cost、Stage Timeout 和 Retry 上限控制。

---

## Q13：Fan-out 多任务并行时怎么保证一致性？

当前实现不是“所有任务随便并行”，而是五条约束：

### 1. DAG Frontier

`execution_waves()` 用 Kahn 拓扑算法计算当前所有入度为 0 的 Stage。缺失依赖或环在运行前被拒绝。

```python
while queue:
    wave = [queue.popleft() for _ in range(len(queue))]
    waves.append(wave)
    for stage_id in wave:
        for child in graph[stage_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
```

### 2. 只读才共享并行 Subject

`RunEngine._run_wave()` 只有在 `len(stages) > 1` 且所有 Stage 都 `read_only` 时才进入线程池。写 Worker 不共享一个可写 Worktree 并发运行。

### 3. Fan-out 前冻结 Subject

所有 `PreparedStage` 在 Worker 启动前构造，记录同一 `subject_digest` 和各自 Context Manifest。事件 `supervisor.fanout.started` 保存 Stage 列表、Subject 和 `merge_order`。

### 4. Fan-in 后验证写集

```python
if stage.read_only and self._subject_digest(prepared.worktree) != prepared.subject_digest:
    valid = False
    self._runtime_failure(
        prepared.run_id,
        stage.id,
        "Read-only worker modified the shared subject during execution.",
    )
```

任一 Reviewer 越权写入会导致 Runtime Failure，最终不回写主目录。

### 5. 固定归并顺序

Provider 调用并行，但 Commit/Evidence 按 Workflow 中的 Stage 顺序进行，避免“谁先完成谁先写事件”造成非确定性。普通 Review 和 Security Review 使用不同 Requirement，各自绑定同一个最终 Subject。

### 当前限制

- 两个只读 Worker 仍共享同一个 Worktree；它通过执行后检测防越权，不是操作系统级只读挂载。
- 若未来需要并行写入，应为每个 Worker 创建子 Worktree，生成独立 Patch，再做路径所有权检查、三方合并和合并后全量测试。当前没有实现，所以不能声称支持并行写 Worker 的一致合并。

---

## Q14：如何解决死循环或沟通低效？

### 防死循环

- DAG 启动前做环检测，静态依赖不允许成环。
- Repair Loop 固定最多两轮。
- Provider Stage 有 Timeout。
- Profile 有最大 Cost 和 Retry。
- 输出 Schema 错误直接失败，不通过“再聊一轮看看”无限修正。
- Human Gate 持久化为 Pending，不在后台忙等。
- Opaque Running Stage 恢复时 Fail-closed，不自动重复副作用。

### 降低沟通开销

- Worker 不互相自由聊天，Supervisor 只传结构化结果。
- 普通 Review 与安全 Review 各自聚焦一个维度，避免一个超长 Prompt 同时承担所有判断。
- Context Pack 只保留前序事实、Repo Map 和 Top-K Verified Memory。
- stdout/stderr 只保留摘要和 Digest，完整大输出不反复塞回模型。
- Fan-in 用稳定 Schema，而不是让 Supervisor 从多段自然语言中猜结论。

### 进度判断依赖环境反馈

Coding Agent 很适合有“测试 Oracle”的任务。Anthropic 对 Agent 的建议也是让 Agent 持续从环境获得 Ground Truth，并设置停止条件；SWE-agent 则把 Agent 与执行环境分开并保存 Trajectory。[Anthropic Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)、[SWE-agent Architecture](https://swe-agent.com/0.7/background/architecture/)

---

## Q15：自研 Supervisor DAG 的难点与处理

### 难点一：确定性拓扑与条件阶段共存

Workflow 先做静态拓扑；`profile.strict` 和 `review.has_blockers and loop < 2` 是白名单条件表达式，不执行任意 Python。这样既能分支，又不会把配置变成代码执行入口。

### 难点二：状态先落库还是先做副作用

每个 Provider 调用前先把 Stage 标为 Running；完成后再持久化输出和证据。Resume 跳过 Completed。若进程在 Opaque Provider 调用中崩溃，系统知道它停在 Running，但不知道外部副作用是否完成，因此拒绝自动重放。

### 难点三：并行时 SQLite 与事件顺序

SQLite Connection 没有被多个 Worker 线程直接共享。主线程先 Prepare；线程池只执行外部 Provider；主线程再按稳定顺序 Commit、写 Artifact 和 Append Event。这避免并行 `MAX(sequence)+1` 竞争和事件链顺序不确定。

### 难点四：模型输出不能控制调度结论

Provider 可以返回 Plan/Test/Review 内容，但 `delivery_decision`、`confidence`、`evidence` 和 `has_blockers` 等决策字段被禁止。`has_blockers` 由 Runtime 根据结构化 Finding 中未解决的 High Severity 推导。

### 难点五：Repair 后旧证据失效

Fix 会改变 Subject。旧 Test/Review 虽然保留在事件链中，但 Gate 的 `subject_selector=run` 只选择最终 Subject 对应的记录。严格模式 Fix 后会重新 Test，并重新 Fan-out 普通和安全 Review。

### 难点六：恢复时 Policy 漂移

Run 创建时冻结 Workflow 和 Policy；Resume 不重新读取新的项目 Policy。这避免一个待批准 Run 在规则变化后被不同标准放行。

### 为什么称为“小型 Supervisor”，不是通用框架

它支持固定 DAG、Frontier、有限条件、只读 Fan-out、人工中断和有界修复，但不支持用户动态注册任意 Node、Reducer、分布式队列或跨机器 Worker。边界越清楚，简历越可信。

---

## Q16：Git Worktree 隔离算不算亮点？难在哪？

### 单独调用 `git worktree add` 不算强亮点

Git Worktree 是成熟能力，Claude Code 也已经原生支持并行 Session/子 Agent Worktree。亮点在于它是否与 Run ID、恢复、Evidence、Subject、Fail-closed Gate 和回写策略形成闭环。[Claude Code Worktrees](https://code.claude.com/docs/en/worktrees)

### muxdev 当前做法

- Git 仓库：创建 `<worktrees-root>/<run-id>` 和分支 `muxdev/<run-id>`。
- 已存在且完整：作为 Durable Run Worktree 复用。
- 路径存在但不完整：抛出 `ReconciliationRequired`，不擅自删除现场。
- Git Worktree 创建失败：复制项目到 Run 目录，初始化 Git 并提交 Baseline。
- 非 Git 项目：同样使用隔离 Copy + Baseline。
- Copy 时排除 `.git`、`.muxdev`、测试缓存、运行目录和嵌套 muxdev Home，避免递归复制。
- Gate PASS 才把变更文件复制回主工作区。

### 实际坑

1. **分支/路径碰撞**：上次崩溃留下 Branch 或 Worktree，不能直接覆盖。
2. **未跟踪文件**：标准 Git Worktree 不带主目录的未提交/未跟踪文件；这既保证 Baseline 清晰，也可能缺少本地配置。
3. **`.env` 和依赖目录**：复制敏感文件有泄漏风险，不复制又可能无法运行。当前 muxdev 不自动复制 Secret，需项目显式准备。
4. **嵌套运行目录递归**：Fallback Copy 若把 `.muxdev/runs` 也复制，会指数增长。
5. **Windows 文件锁和脚本入口**：删除、清理、`.cmd/.bat` 启动与隐藏窗口都需要平台处理。
6. **回写冲突**：Run 执行期间主工作区可能变化。当前 `apply_changes()` 是文件级复制，没有做三方 Merge，这是明确限制。
7. **只读 Reviewer 越权**：Worktree 隔离只能保护主目录，不能阻止 Reviewer 改 Run Worktree，所以又增加 Subject 前后校验。

面试表达应是：Worktree 不是创新算法，但它是可信交付边界的重要基础设施；难点在生命周期、恢复、回写和与证据绑定。

---

## Q17：【待完成】Provider Action 与 muxdev Approval 两套阻塞模型

### 当前状态：muxdev Approval 已实现，Provider Action 尚未接通

这是必须诚实标记的待完成项。

### Provider Action 是什么

Provider 在自己的 Turn 内准备执行高风险动作，例如：

- 执行 Shell 命令。
- 修改文件。
- 请求网络或额外目录权限。
- 调用需要批准的 MCP Tool。
- 向用户追问参数。

Codex app-server 会发起双向 JSON-RPC Approval Request，客户端必须在同一协议会话上回应，然后 Provider Turn 才继续。Claude Code 也有 PermissionRequest/PreToolUse 等机制，但非交互 `-p` 的事件行为不同。

### muxdev Approval 是什么

它是 Workflow/业务层 Checkpoint，例如“批准计划”或“确认严格交付”。它的生命周期独立于 Provider：

```text
interaction.requested
  → pending
  → approved / rejected / responded
  → RunEngine.resume
```

它被保存为 SQLite Interaction 和 `InteractionEvidence`，进入 EvidencePolicy。

### 为什么不能把两者混成一个 Approval

| 维度 | Provider Action | muxdev Approval |
|---|---|---|
| 发起者 | 某个 Provider Turn | muxdev Workflow |
| 目的 | 决定某个工具动作能否继续 | 决定交付流程能否越过业务 Checkpoint |
| 生命周期 | 通常要求保持或恢复 Provider Session | Run 级持久化，可跨进程恢复 |
| 决策选项 | accept/decline/session permission 等 | approved/rejected/responded |
| Evidence 含义 | 某动作获得权限 | 某交付 Requirement 获得人工决定 |

### 当前代码缺口

`StageExecutionResult` 已预留 `interaction_requests`，但 `HeadlessCliProviderAdapter` 仍使用一次性 `subprocess.run()`，没有保持双向 app-server/SDK 通道，也没有把 Provider Request 持久化和回送。因此当前配置倾向 `--ask-for-approval never` 或 Provider 的非交互模式。

### 合理后续方案

1. 为每个 Provider 定义 `ProviderActionRequest` 联合类型，保留原始 Provider Request ID。
2. Adapter 改为可流式、可暂停的 Session Port，而不是只有一次性进程。
3. Provider Action 写独立 Interaction Kind，不自动满足 `human_approval` Requirement。
4. Resume 时先恢复 Provider Session，再回送原 Request ID；无法恢复则标记 Reconciliation Required。
5. 对超时、断连、重复响应和 Session 过期做幂等处理。

在完成前，不能声称 muxdev 已统一接管不同 CLI 的内部权限弹窗。

---

## Q18：可信任交付链路由哪些部分组成？技术上做了什么？

可以按八个环节理解：

### 1. 冻结输入

保存 Task、Workflow、Profile、Policy Hash、Provider Route、Cost 和 Worktree。恢复时不被新配置悄悄改变。

### 2. 隔离执行

Run 只在自己的 Worktree 中修改；主目录是交付目标，不是 Agent 随意工作的现场。

### 3. 最小权限阶段

Workflow Stage 声明 Read-only、Allow-write、Allow-shell；Skill 绑定经过权限校验，不能扩大 Stage Authority。Runtime 还用 Subject 前后比较验证只读承诺。

### 4. 结构化 Provider 契约

统一 `StageExecutionInput/Result`，Plan/Change/Test/Review 用 Pydantic 校验；禁止模型控制 Gate 字段。

### 5. Runtime Ground Truth

Runtime 计算 Diff、Subject、Artifact Digest，并重放测试命令。Provider 的输出只是 Claim。

### 6. 独立验证

Review 绑定 Subject；Standard/Strict 要求 Reviewer 与 Executor 不同；Strict 另有 Security Review 和 Human Approval。

### 7. 确定性 Gate

EvidencePolicy 是唯一硬规则源。Scorecard 只解释 Completeness、Reproducibility、Integrity、Independence，不能覆盖 Blocker。

### 8. 完整性与交付

Evidence Event 使用哈希链；报告保存 Records Hash；可选 DSSE 绑定报告摘要。只有 `PASS` 才 Apply Changes。

这个链路与软件供应链 Provenance 的思想相似：记录某个 Artifact 在何时、由谁、如何产生，并把声明绑定到 Subject。muxdev 借鉴 in-toto 的 Predicate/Statement/Envelope 分层，但不宣称达到 SLSA 等级或 in-toto 合规。[in-toto Attestation](https://github.com/in-toto/attestation/blob/main/spec/README.md)、[SLSA Provenance](https://slsa.dev/spec/v1.2/provenance)

---

## Q19：可信流水线与主动用提示词约束 Claude Code 有什么区别？

### 提示词约束是概率性的

例如提示：

> 修改完成后一定运行测试；测试失败必须修复；必须让另一个 Agent 审查；没有批准不要提交。

它能显著提升行为质量，但最终仍由模型决定是否遵守、如何解释“测试”和“另一个 Agent”。上下文压缩、冲突指令、模型升级或 Tool Error 都可能影响执行。

### muxdev 约束是运行时的

- 没有 `CheckEvidence`：Requirement Missing。
- 实际退出码非 0：Requirement Failed。
- Reviewer 等于 Executor：Independent Review Failed。
- Target Digest 不是最终 Subject：Review Failed。
- 没有批准：Waiting Human。
- 有 High Finding：Blocked。
- Gate 非 PASS：代码不回写。

这些判断是 Python 代码和冻结数据模型，不取决于模型“是否同意”。

### Claude Hooks 能否做到确定性？

可以部分做到。Claude 官方明确把 Hooks 定义为生命周期固定点上的确定性控制，可阻止工具、格式化、记录审计、要求批准。这比纯 Prompt 强很多。[Claude Code Hooks](https://code.claude.com/docs/en/hooks-guide)

差别在于 muxdev 已把 Hook 类能力进一步产品化为：

- 跨 Provider 的统一 Schema。
- Run/Stage/Interaction 生命周期。
- Frozen EvidencePolicy。
- Subject-bound Review。
- Runtime Replay。
- Gate Report 和 Attestation。

如果团队只用 Claude Code，精心设计 Hooks 完全是合理方案；muxdev 的优势主要出现在跨 Provider 和统一交付治理，而不是否认 Hooks 的能力。

---

## Q20：Memory 治理如何做，解决了什么问题？

### 当前方案：Evidence-grounded Derived Memory

旧版本曾有泛化 Memory/RAG 平台，后来因为表多、Authority 不清和难以证明有效而删除。本轮只恢复一个窄而有价值的能力：从可信交付事实派生长期上下文。

### 写入/晋升规则

muxdev 没有提供 `memory.write("一句经验")` 让模型任意写长期事实。历史 Run 只有同时满足以下条件才成为候选：

1. Run Status 是 `completed`。
2. Evidence Report 仍能通过 Schema 校验。
3. Gate 可以从 Frozen Policy 与 Records 重算一致。
4. Artifact Digest、Records Hash 和 Event Chain 有效。
5. Gate Status 是 `PASS`。

### 检索规则

- 候选文本由 Task 和结构化 Stage Summary/Affected Paths 组成。
- 本地 BM25 排序，不需要外部 Embedding 服务。
- 只取 Top 3 且分数必须大于 0。
- 返回内容带 Run ID、Subject Digest 和摘要。
- 任务文本经过 Secret Redaction。

### 注入规则

Verified Memory 不是每次全量加载，而是 Context Pack 的最后一层；前序事实和 Repo Map 优先，总包最大 12,000 字符。Manifest 记录命中的 Run IDs 和 Context Digest。

### 解决的问题

- **污染**：失败尝试、被篡改报告和未批准结论不会晋升。
- **陈旧性**：每次检索都重新验证，不是一次晋升永久可信。
- **不可追溯**：命中带来源 Run 和 Subject。
- **上下文爆炸**：Top-K + Budget + Structured Summary。
- **Authority 混乱**：Memory 只影响 Prompt，不改变 EvidencePolicy 和 Gate。

### 没解决的问题

- 当前 BM25 对语义同义词不如 Embedding。
- 没有 TTL、人工 Pin/Forget 和项目版本兼容策略。
- 只构建派生视图，没有独立 Memory 表和增量索引。
- 仍需评测“检索是否提高任务成功率”，不能仅凭架构声称效果。

---

## Q21：Memory 分层有哪些？每层记什么、怎么存？

当前可以分五层：

| 层 | 内容 | 存储 | 是否进入 Prompt | 是否能决定 Gate |
|---|---|---|---|---|
| L0 Policy Memory | Workflow、Profile、Evidence Requirement、Stage 权限 | YAML；Run 创建时冻结进 SQLite Metadata | 以 Schema/权限形式进入 | 是，只有 Frozen Policy 决定硬门禁 |
| L1 Working Memory | 当前 Run 已完成 Stage 的结构化 Result | SQLite `stages.result` + Stage Artifact | 是，压缩后进入下游 Stage | 否 |
| L2 Structural Memory | 路径、Python 类/函数签名、与任务词相关的 Repo Map | 按需从 Worktree AST 重建 | 是，最大约 6,000 字符 | 否 |
| L3 Verified Episodic Memory | 历史 PASS Run 的 Task、Subject、Stage Summary、Affected Paths | 从 SQLite + `evidence-report.json` 派生，不另建真相表 | Top-K 命中时进入 | 否 |
| L4 Audit History | 全量 Evidence Record、Interaction、事件链、Artifact、Route、Outcome | 12 表 SQLite + Run 文件目录 | 默认不全量注入 | 是，作为 Gate 的事实输入 |

两个设计原则：

1. **Memory 不是新真相源**：L3 可随时从 Evidence Report 重建。
2. **记忆与权限分离**：即使历史记忆说“以前都直接上线”，也不能改变当前 Frozen Policy。

这与 Claude Code 的 Project Memory/Auto Memory 有相似目标，但 muxdev 的 L3 只接受通过完整性验证的交付事实。Claude 官方说明 Auto Memory 是模型根据纠正和偏好写入的 Markdown，首 200 行或 25KB 会加载；muxdev 不复刻这种通用个人偏好系统。[Claude Code Memory](https://code.claude.com/docs/en/memory)

---

## Q22：上下文压缩做了什么？

### 为什么要压缩

多 Agent 系统如果把 Plan 原文、所有代码、完整测试日志、所有 Review 和历史 Transcript 全部传给每个 Worker，会出现：

- Token 成本和延迟增长。
- 关键约束被噪声淹没。
- 前一 Agent 的错误推断被无差别传播。
- Provider 的上下文窗口和格式差异难以统一。

### 当前 Context Pack 的三段式结构

#### 1. Upstream Structured Facts，最多约 3,500 字符

从当前 Run 的 Completed Stage 中提取：

- Stage ID、Role、Provider。
- Summary。
- Pydantic 校验后的 Parsed Output。

不注入完整聊天历史，也不注入隐式推理。

#### 2. Deterministic Repository Map，最多约 6,000 字符

遍历 Python 文件，抽取类/函数签名；根据 Task Token 对路径和符号打分，稳定排序，并限制文件和字符预算。思想借鉴 Aider Repo Map，但当前实现没有 Aider 的依赖图 PageRank。[Aider Repository Map](https://aider.chat/docs/repomap.html)

#### 3. Verified Delivery Memory，使用剩余预算

对仍能验证为 PASS 的历史交付做 BM25，放入最多三条来源明确的摘要。

### 总预算和可审计 Manifest

整个 Pack 最大 12,000 字符。每个 Stage 保存 `context-pack.json`：

```json
{
  "manifest": {
    "schema": "muxdev.context-pack.v1",
    "max_chars": 12000,
    "used_chars": 8342,
    "truncated_sections": [],
    "upstream_stage_ids": ["plan", "implement", "test"],
    "memory_run_ids": ["run_previous_login_001"],
    "repo_map_digest": "sha256:...",
    "context_digest": "sha256:..."
  },
  "text": "..."
}
```

这样可以回答“这个 Reviewer 当时到底看到了哪些压缩上下文”，而不是只说“系统有 Memory”。

### 与 LLM 自动 Summary 的取舍

当前压缩是确定性的字段筛选、字符截断和词法检索，没有再次调用 LLM 做摘要，优点是便宜、稳定、可重建；缺点是语义压缩能力有限。SWE-agent 通过 `HistoryProcessor` 压缩 Agent History；Claude Code 和 Codex 也有自动 Context Compaction。muxdev 当前选择更窄：不管理 Provider 内部 Conversation，只管理跨 Stage 的交付上下文。[SWE-agent Architecture](https://swe-agent.com/0.7/background/architecture/)、[Claude Code Context Window](https://code.claude.com/docs/en/context-window)

### 后续合理演进

1. 增加 Tokenizer 感知预算，替代字符近似。
2. 为非 Python 语言增加 Tree-sitter Symbol Map。
3. 在候选集足够大时，增加可选 Embedding + BM25 Hybrid Retrieval；Embedding Index 仍只是可重建 Cache。
4. 增加 Retrieval Eval：命中率、上下文利用率、成功率变化、Token 节省和错误记忆率。
5. 为 Project Version/Branch 增加兼容过滤，避免检索已经不适用的旧架构。

---

## 面试时建议的最终总结

可以用下面这段话收束：

> 我没有把 muxdev 做成另一个 Coding Agent，而是把 Codex、Claude Code 等 Agent 放进一个可信交付控制面。核心设计是把 Provider 声明与 Runtime 事实分开：Agent 负责计划、实现和提出测试，Runtime 负责隔离 Worktree、重放命令、计算 Subject、验证独立审查并执行 Frozen EvidencePolicy。本轮我又补上了 Provider 协议编解码、只读 Review Fan-out/Fan-in，以及只从可验证 PASS 交付中检索的 Evidence-grounded RAG。系统不会靠分数覆盖硬门禁，也不会把 Evidence Chain 夸成尚未实现的任意节点回滚；Provider 内部 Action 的统一暂停/恢复仍明确标记为待完成。这使项目既有 AI Agent 编排、Context Engineering、RAG 和多 Provider Adapter 的面试深度，也有真实可落地的交付价值。
