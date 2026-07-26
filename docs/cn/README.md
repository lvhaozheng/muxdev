# muxdev 中文设计与运维指南

面向日常操作的说明请先阅读：[muxdev 产品使用手册](product-user-manual.md)。

Skills 来源、冻结、审计加载和 Evidence 门禁见：[Skills 与可信交付设计](skills-trusted-delivery.md)。

## 1. 设计理念

muxdev 是 Coding CLI 的薄控制面，不是 Agent SDK wrapper。它不重建 Codex 或 Claude Code 的记忆、工具协议、Skill、MCP、Plan Mode 和审批；它只管理 CLI 进程之外必须由产品负责的事实：Conversation、PTY、Assignment、权限、工作树、合并、Evidence、交付候选与接受后写回。

角色提示只影响行为，不是安全边界。argv/env 白名单、Assignment 范围、worktree 隔离、确定性检查和交付 Gate 由 Runtime 强制执行。

Workbench 是当前用户的单例 Daemon。`muxdev serve` 把当前目录登记为项目；已有健康实例时只输出该项目深链并退出。全局库只保存项目登记、Rule、设备认证和 Daemon 状态；Conversation、Run、Evidence、ChangeSet 和 transcript 仍留在各项目的 `.muxdev/control.sqlite` 与制品目录。HTTP、SSE、Terminal 和文件接口均以 `project_id` 为边界。

## 2. Agent、Conversation、Assignment

- `CliAdapterDefinition` 描述完整 CLI 的启动 argv、恢复 argv、工作目录和模型参数、环境变量白名单、PTY/resize/resume 能力与 session ID 发现方式。
- `AgentDefinition` 描述显示名、CLI、模型、角色提示、能力标签、编排资格、最大并发和默认权限。
- 内置 Conversation Agent 目录覆盖 Codex、Claude Code、Deep Code、Qwen Code、Kimi Code、Trae 和 Antigravity。Agent Registry 扫描 daemon `PATH`，新建窗口按“已检测到 / 未就绪”展示；检测结果同时用于创建前校验和实际进程启动。Deep Code 通过真实 PTY/ConPTY 启动，支持原生 `--resume <session-id>`，并识别 `.deepcode/skills` 与 `.agents/skills`。
- 内置 Codex Session 使用 `workspace-write` 与 Approve for me（`--ask-for-approval never`）；为避免 `muxdev collab` 反复审批，只额外授予项目 `.muxdev` 控制目录写权限，不使用会关闭整个沙箱的危险 bypass 模式。
- `Conversation` 是用户可见的长期协作容器，拥有模式、主 Agent、编排者、活动计划、集成工作树和 DeliveryContract。
- `Interaction` 保存澄清问题、回答和 Run 中途提问；它不是 Run。
- `Assignment` 是边界明确的 `consult/write/review` 执行段；每个 Assignment 唯一对应一个 Run，重试和重派记录为同一 Run 的 Attempt。
- `AgentSession` 按 `Conversation × Agent × lane` 管理逻辑终端。默认复用 `main` lane，并发或隔离写任务使用临时 lane。
- `SessionGeneration` 记录每次物理进程启动、原生恢复或上下文重建；Generation 改变时逻辑 Session ID 和 transcript 序号不变。

Conversation 先进入 `clarifying`。需求不完整时只创建 1–3 个 Interaction；需求冻结为 `ready` 后才自动执行。普通消息默认进入主 Agent Session，`@agent` 只加入协作，显式咨询、写任务或评审才创建 Assignment Run。Agent 上下文包不超过 12,000 字符，优先保留冻结契约、用户决策、未决问题、依赖产物和仍通过 Evidence 校验的交付记忆。

项目 schema v14 的 Conversation Memory Checkpoint 记录覆盖序列、源哈希、目标、约束、人工决定、Assignment 结果、验证状态和未解决问题。Run 结算或未压缩尾部超过约 8,000 token 时生成；纠正只追加新版本。所有 Agent 注入同一共享检查点，再叠加各自 transcript、Assignment 和依赖输出。

## 3. 直接模式与编排模式

`direct` 是默认路径：主 Agent 使用 Conversation 集成工作树端到端工作。Standard 完成后按需启动独立 Reviewer；Strict 再增加 Security Reviewer 和人工确认。

`orchestrated` 必须显式选择具备 `orchestrate` 能力的 Agent。编排者提交 `muxdev.orchestration-plan.v1`：

```json
{
  "schema_version": "muxdev.orchestration-plan.v1",
  "summary": "implementation and review",
  "max_parallel": 4,
  "nodes": [{
    "id": "implementation",
    "title": "Implement feature",
    "brief": "bounded task brief",
    "agent_id": "codex",
    "role": "implementer",
    "dependencies": [],
    "work_mode": "write",
    "deliverables": ["source changes"],
    "completion": ["acceptance tests pass"],
    "proof": ["ChangeSet and Runtime check"]
  }]
}
```

Runtime 校验无环依赖、Agent、能力、并发、范围和三段式标准后展示 DAG。用户确认一次即冻结计划。确认后允许在原目标、范围、预算、权限、并发和标准内拆分、重试、重派与调整依赖；扩大边界必须重新确认。

## 4. 工作树、合并和恢复

每个并行写 Assignment 从依赖已完成的集成状态创建子 worktree。完成后生成内容寻址 ChangeSet。合并顺序为拓扑顺序，其次稳定 Assignment ID；触碰文件的当前 hash 与节点基线不一致时拒绝覆盖，并创建冲突解决 Assignment。

每个 Assignment 最多使用两次安全恢复额度。超过额度后转为 `blocked`。所有子 Agent 都不能直接写回用户项目。

daemon 启动时会 reconcile 孤立 Session。tmux 直接 reattach；普通 PTY 使用 CLI 原生 session ID；不支持 resume 的 CLI 会用任务简报、依赖产物和 transcript 摘要启动新 Generation，并标记 `rebuilt_context`。Windows 缺少 ConPTY 时实际后端为 `pipe`，Doctor、API 和界面会一致禁用 resize/resume。

## 5. Web 终端和远程安全

Dashboard 使用本地打包的 xterm.js 与 fit addon，不依赖 CDN。WebSocket JSON 帧：

- 客户端：`attach`、`input`、`resize`、`release_write`；
- 服务端：`output`、`status`、`lease`、`error`。

终端 transcript 是带序号的本地 JSONL，权限尽量限制为所有者读写。Web attach 默认只读；用户点击“获取控制权”后才申请写租约。一个 Session 只有一个有效写入租约，其他设备只读。输出断线重放使用 `after_seq`。输入单帧限制 64 KiB、10 秒累计 1 MiB；resize 去重后限制为 20 次/10 秒，其他控制帧为 30 次/10 秒，合法 heartbeat 不占控制桶。限流不会立即释放写租约，连续五次违规才关闭连接。`interrupt` 只向当前前台命令发送 Ctrl+C/SIGINT，不关闭 Session。

远程模式必须启用 HTTPS 反向代理或 VPN，并完成一次性设备配对。WebSocket 不依赖 HTTP 中间件，自己校验 Cookie、Origin、Session、帧大小和速率。

Dashboard 提供已配对设备列表和撤销操作；撤销设备会同时使它的 Web Session 失效。终端标题持续显示写租约状态，接管操作会明确覆盖其他设备的有效租约。

## 6. 三段式团队交付标准

普通用户先选择代码修改、指定文件、报告/文档、可运行应用/API、分析回答或其他；系统将其编译为 `muxdev.delivery-standard.v2`。默认界面只显示“交付什么、是否达标、还缺什么”，阶段、Verifier、契约和 Evidence 留在高级视图。每一项包含：

- `deliverable`：需要交付的内容；
- `completion`：可判定的完成条件；
- `proof`：可信证明方式；
- 可选 `assignment_id`；
- `verifier`：`runtime_check`、`agent_review`、`artifact` 或 `human_acceptance`。

内置基线不可删除或降级。Runtime check 只能引用 Workflow 中冻结的 argv 命令；Agent review 必须绑定 Reviewer 身份和当前 Subject；artifact 必须内容寻址；人工接受必须是结构化事件。

标准修改会生成新 Contract、记录受影响 Assignment 并使旧 Candidate 失效。已接受的 Candidate、Contract 和 Evidence 不会被改写。

`ReviewRecordV1` 以不可变记录保存三类人工事实：`interaction`、`decision` 和带文件/行号引用的 `change_request`。Rule 分为代码规范、CI 门禁、文档模板与交付标准，来源可以是内置、个人全局或项目绑定；Conversation 创建或修订时冻结具体版本。CI Rule 只能引用 Workflow 已登记的 argv 命令。Memory Candidate 批准后生成 Rule 草稿，不自动修改 `MUXDEV.md`。

## 7. Agent 配置与排错

```powershell
muxdev agent list
muxdev agent show codex
muxdev agent doctor codex
muxdev doctor
```

常见降级：

- `executable not found`：在本机安装 CLI，或在项目配置中禁用该 Agent。
- `pipe` 后端：系统没有可用 PTY；Agent 仍可 headless 工作，但 resize 和交互能力会显示不可用。
- `resumable`：daemon 已重启，使用终端重新附着即可触发原生恢复或上下文重建。
- `merge_conflict`：集成文件偏离节点基线，必须完成 Runtime 创建的冲突解决 Assignment。
- `delivery.blocked`：缺少独立 Reviewer、检查失败、Evidence 不完整或标准未满足；查看 Candidate Evidence 的 blocker。

## 8. 迁移与兼容

旧数据库打开时幂等迁移到项目 schema v14，并在 v12→v13 前创建 SQLite 备份。项目数据不会移动到全局数据库。无 `project_id` 的旧 v2 路由不再提供；`/api/v1`、`/runs`、ACP/MCP、headless Provider 与固定四工作流保持兼容。

## 9. 与 botmux 的关系

muxdev 参考 botmux 的薄编排和 CLI 进程思路，不复制代码。botmux 的飞书话题和群成员路由，在这里由本地 Conversation、Assignment、AgentSession 与共享时间线取代。muxdev 额外提供隔离 worktree 的 fail-closed 合并、Evidence v3、三段式交付标准和接受后冲突安全写回。
