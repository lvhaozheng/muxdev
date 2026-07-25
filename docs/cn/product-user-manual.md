# muxdev 产品使用手册

> 适用范围：当前 `release/v4` 工作区的 Conversation Web 工作台与 CLI。  
> 默认场景：单个开发者在本机或受信设备上，使用 Codex、Claude Code、Qwen Code、Kimi Code 等 Coding CLI 完成可审查、可验证、可接受或安全回退的开发任务。

## 1. muxdev 是什么

muxdev 是 Coding CLI 外围的可信交付控制面。它不会替代 Agent 自己的工具、记忆、MCP 或审批机制，而是把一次开发任务组织成可持续使用的 Conversation：

```text
描述目标
  → 必要时澄清
  → Agent 执行
  → 持续记录活动和文件变化
  → Runtime 验证
  → 人工评审
  → 接受 / 请求修改 / 回退
```

每个 Conversation 会保留同一任务的多轮 Run、Agent Session、文件变化、验证历史和人工决定。接受、回答或回退一轮后，Conversation 会回到“待命”，以后可以继续发起新一轮工作；只有“已关闭”才是会话终态。

### 1.1 适合使用 muxdev 的任务

- 修改代码并运行测试；
- 生成或更新指定文件；
- 完成报告、文档或分析；
- 构建可以运行的应用或 API；
- 把复杂目标拆成多 Agent DAG 并行执行；
- 对 Agent 产出进行逐文件评审、请求修改、接受或回退。

### 1.2 三个最重要的概念

- **Conversation**：一个长期任务空间。目标、消息、执行轮次和评审历史都留在这里。
- **Run**：Conversation 中的一轮实际执行。请求修改会创建下一轮 Run，但继续复用原 Conversation 和逻辑 Agent Session。
- **Delivery Candidate**：已经完成最终对账和验证、等待你决定的本轮交付候选。

## 2. 安装与启动

### 2.1 环境要求

- Python 3.11 或更高版本；
- 至少安装并配置一个可用的 Coding CLI；
- Web 前端已随 Python 包构建，正常使用不要求本机安装 Node.js。

在项目根目录执行：

```powershell
python -m pip install -e ".[test]"
muxdev init
muxdev agent list
muxdev agent doctor
muxdev serve
```

如果当前终端找不到 `muxdev` 命令，可以使用等价形式：

```powershell
python -m muxdev init
python -m muxdev agent list
python -m muxdev agent doctor
python -m muxdev serve
```

`serve` 默认把执行命令时的当前目录登记为项目，并输出该项目的可点击深链，例如：

```text
http://127.0.0.1:8765/projects/project_...
```

Workbench 未运行时，当前命令启动用户级单例 Daemon 并保持前台运行。Workbench 已运行时，命令只登记或选中当前项目、输出 Daemon 的实际 URL，然后正常退出；不会再次占用端口。因而可以在另一个项目目录继续执行：

```powershell
cd D:\path\to\another-project
muxdev serve
```

指定其他工作区或首次启动所用端口：

```powershell
muxdev serve --workspace D:\path\to\project --port 9000
```

### 2.2 启动前检查 Agent

先确认至少一个 Agent 显示为可用：

```powershell
muxdev agent list
muxdev agent doctor --agent-id codex
```

Web 新建任务窗口会禁用未安装的 Agent，并显示不可用原因。多 Agent 编排模式还要求主要 Agent 具备 `orchestrate` 能力。

内置可发现目录包括 Codex、Claude Code、Qwen Code、Kimi Code、Trae 和 Antigravity。每次打开“新建任务”，工作台都会重新扫描 daemon 的 `PATH`：

- “已检测到”中的 Agent 可以直接作为主 Agent；
- “未就绪”中的 Agent 尚未安装、daemon 看不到其命令，或缺少所需 PTY；
- Windows 安装 npm CLI 后通常会检测到对应 `.cmd` / `.ps1` 启动器；
- 安装新 CLI 后若仍未出现，请从能够执行该命令的终端重启 muxdev daemon。

## 3. 五分钟完成第一个任务

1. 启动 `muxdev serve` 并打开工作台。
2. 点击左侧“新建任务”。
3. 在“任务目标”中描述问题、期望结果和约束。
4. 选择可用的主要 Agent。
5. 首次使用建议选择：
   - 协作方式：`单 Agent 直达`；
   - 最终想拿到什么：按任务选择；
   - 交付强度：`Standard`。
6. 点击“创建任务”。
7. 如果中间区域出现“需要你确认”，在底部输入答案并按 Enter。
8. Agent 执行时，在右侧查看 Summary、Changes、Terminal 和 Review。
9. 进入“可评审”后：
   - 结果符合预期：点击“接受变更”；
   - 需要调整：填写修改意见并点击“请求修改”；
   - 不保留本轮：点击“回退本轮”。

推荐的任务目标写法：

```text
修复用户登录后刷新页面会退出的问题。

期望结果：
1. 登录状态在刷新后保留；
2. 不改变现有登录接口；
3. 增加覆盖刷新场景的测试；
4. 运行相关测试并提供验证结果。

限制：
- 不新增外部依赖；
- 不修改 admin 模块。
```

## 4. 工作台界面

工作台由 Project Rail、Conversation Rail、中间 Conversation、右侧 Tool Canvas 和底部操作区组成。

### 4.1 最左侧：Project Rail

Project Rail 显示已登记的本地项目、目录健康状态、需要处理的事项和运行中 Agent 数。切换项目只切换页面上下文；Conversation 数据、终端、SSE、文件和 Agent Session 始终按项目隔离。

从页面登记项目只保存目录引用，不扫描磁盘，也不会移动项目。删除登记同样不会删除项目目录或项目内的 `.muxdev` 数据。

### 4.2 左侧：Conversation Rail

| 分组 | 含义 | 你通常要做什么 |
|---|---|---|
| Needs You | 有问题、审批或其他事项等待你处理 | 打开 Conversation 并响应 |
| Active | 正在澄清、执行、验证或恢复 | 观察进度，必要时补充上下文 |
| Ready | 本轮已有可评审交付 | 检查 Changes 和 Review |
| History | 已回答、已接受、已回退、待命或已关闭 | 查阅历史或继续下一轮 |

可以使用搜索框按标题或目标筛选 Conversation。

### 4.3 中间：Conversation

Conversation 顶部显示：

- 会话标题；
- 主要 Agent；
- 单 Agent 或多 Agent 模式；
- 当前 Run；
- 当前用户语言状态。

时间线按顺序展示需求、消息、Assignment、文件变化、验证和交付事件。每条活动还会标明证据等级：

- **已记录**：由系统事务直接记录；
- **已观察**：由运行期文件监听等机制观察到；
- **已验证**：已经过最终文件树对账或 Runtime 验证。

“已观察”不等于最终交付事实。评审时应以 Changes 中的已验证结果和 Review 中的验证历史为准。

### 4.4 底部：Composer

Composer 用于：

- 回答澄清问题；
- 给正在工作的 Agent 补充上下文；
- 调整方向；
- 在一轮结束后继续发起下一轮工作。

快捷键：

- `Enter`：发送；
- `Shift + Enter`：换行。

当页面显示待回答的澄清问题时，发送内容会作为该问题的答案，并进入冻结的交付要求。

### 4.5 右侧：Tool Canvas

#### Summary

显示当前 Conversation 状态、关注等级、变更文件数、当前验证数、活跃 Session 数和可见 Fleet。

#### Changes

显示本轮已验证的文件净变化：

- 文件列表；
- 总增删行数；
- 单文件增删行数；
- `diff`、`current`、`baseline` 三种只读视图。

说明：

- `diff`：本轮基线与当前内容之间的补丁；
- `current`：本轮工作树中的当前内容；
- `baseline`：Run 开始前的内容；
- 文本预览最多 1 MiB，超过后会标记截断；
- 二进制文件只展示元数据；
- 密钥类敏感路径不会返回文件内容。

#### Terminal

显示当前 Conversation 中的逻辑 Agent Session。选择 Session 后点击“打开终端”。

终端状态包括：

- `连接中`：正在建立 WebSocket；
- `可写`：当前设备持有写入租约；
- `只读`：其他设备持有写入租约；
- `失败输出（只读）`：进程已失败，只重放 transcript，不授予写入租约；
- `已断开`：连接已关闭。

终端打开后默认只读。只有点击“获取控制权”后，当前设备才申请写入租约；同一 Session 同时只有一个有效写入租约，页面会在持有租约期间自动续租。点击“中断命令”只向当前前台命令发送 Ctrl+C/SIGINT，不会关闭 Session 或 Conversation。终端输出可断线重放，但原始终端输出不会自动被当成验证证据。

`resumable` Session 点击“恢复并打开”会自动创建下一 Generation。`failed` Session 不会自动重启：先查看失败输出，再点击“重新启动”。

Windows 没有可用 ConPTY 时，Codex、Claude Code 等声明 `requires_pty` 的 Agent 会显示为不可用，避免把必然失败的进程伪装成可执行。只有明确兼容管道的 Agent（例如测试用 mock）才允许使用 `pipe`。

#### Review

显示：

- 本轮结果摘要；
- 当前评审状态；
- 按 Run 保存的澄清/审批、接受/拒绝/回退决策和带文件行号的修改请求；
- 每次 Runtime 验证的命令、状态、时间和耗时；
- 已过期或已被下一轮取代的历史验证；
- “请求修改”输入区。

常见验证新鲜度：

- `当前`：针对当前文件内容执行；
- `stale`：执行后代码又发生了变化；
- `superseded`：对应 Run 已被下一轮取代。

#### Rule

显示内置与个人全局 Rule 库、项目默认绑定、当前 Conversation 已冻结版本、门禁状态和版本差异。点击“自定义 Rule”可以填写 Rule ID、标题、类型、强度以及门禁内容与交付标准；保存后即可冻结到当前 Conversation。Rule 包括代码规范、CI 门禁、文档模板和通用交付标准。CI Rule 只能引用 Workflow 中已经登记并冻结的 argv 命令，页面不能输入任意 Shell。

## 5. 新建任务字段说明

### 5.1 任务目标

至少说明“做什么”。为了减少澄清轮次，建议同时写清：

- 当前问题或背景；
- 期望结果；
- 允许和禁止修改的范围；
- 必须通过的测试或验收条件；
- 兼容性、性能或安全限制。

### 5.2 主要 Agent

主要 Agent 负责需求判断和主 Session。Agent 下拉框会同时显示：

- 是否启用；
- 本机是否找到对应 CLI；
- 在编排模式下是否具备编排能力。

不可用的 Agent 不能提交任务。

### 5.3 协作方式

#### 单 Agent 直达

适合：

- 范围清晰的常规修改；
- 单模块问题；
- 文档、报告和分析回答；
- 希望快速得到第一轮结果的任务。

主 Agent 在 Conversation 集成工作树中端到端执行。交付标准需要时，Runtime 会追加独立 Reviewer。

#### 多 Agent 编排

适合：

- 可以拆成多个相对独立子任务；
- 需要实现、测试、评审或安全检查并行推进；
- 跨多个模块且依赖关系明确。

编排者会先提出 DAG 计划。计划必须通过无环依赖、Agent 能力、并发和范围校验，用户确认后才执行。首版不会让 Agent 通过终端注入控制其他 Agent。

### 5.4 最终想拿到什么

| 选项 | 适用场景 |
|---|---|
| 代码变更 | 修改项目代码、配置或测试 |
| 分析回答 | 只需要结论或建议，不要求文件变化 |
| 结构化报告 | 需要有明确结构的调查、审计或总结 |
| 指定文件 | 必须产出某个文件 |
| 可运行结果 | 应用、服务、脚本或 API 必须可启动 |

“分析回答”在没有文件变化时会结算为“已回答”，不会制造一个虚假的待评审文件交付。

### 5.5 交付强度

| 强度 | 建议场景 | 核心特点 |
|---|---|---|
| Lite | 低风险、个人草稿、快速迭代 | 最小可信闭环 |
| Standard | 默认选择、一般代码变更 | 确定性检查，按需独立 Reviewer |
| Strict | 高风险、安全或发布前变更 | 更严格门禁、Security Reviewer 和明确人工确认 |

交付强度不是“Agent 更聪明”的开关，而是 Runtime 对验证和人工门禁的要求。

## 6. 完整任务流程

### 6.1 澄清需求

创建任务后，主要 Agent 会判断需求是否完整。

如果不完整：

1. Conversation 状态进入“需要澄清”或“需要你确认”；
2. 中间区域显示待回答问题；
3. 在 Composer 输入答案；
4. 答案进入交付要求；
5. 要求完整后才创建实际 Run。

澄清本身不会创建无意义的 Run。

### 6.2 观察执行

执行期间可以：

- 在时间线查看 Agent 和 Runtime 活动；
- 在 Summary 查看 Assignment/Fleet；
- 在 Terminal 进入某个 Agent Session；
- 从 Composer 继续补充信息。

不要仅根据终端中一句“完成了”判断交付成功。最终状态应以已验证 Changes、Review 和 Delivery Candidate 为准。

### 6.3 评审文件变化

进入 Ready 后：

1. 打开 Changes；
2. 逐个检查变更文件；
3. 使用 `diff` 查看修改，必要时用 `baseline` 和 `current` 对照；
4. 检查增删行数和是否有意外文件；
5. 再打开 Review 查看验证记录。

如果 Changes 显示“没有已验证的文件变化”，可能是：

- 本轮只提供了分析回答；
- Agent 没有产生净变化；
- 运行还没有完成最终对账；
- 运行失败或交付被阻塞。

### 6.4 请求修改

当结果方向正确但仍需调整：

1. 在 Changes 中先选中相关文件；
2. 打开 Review；
3. 输入具体修改意见；
4. 点击“请求修改”。

当前选中文件会作为引用附在意见中。系统会：

- 将旧 Run 标记为“已取代”；
- 将旧验证历史标记为 `superseded`；
- 使旧 Candidate 失效；
- 在同一 Conversation 和逻辑 Agent Session 中创建下一轮 Run。

建议写法：

```text
请保留当前 API 形状，只调整错误处理：
1. 对超时返回 504；
2. 不要吞掉原始异常；
3. 为 src/api/client.py 的重试上限增加边界测试。
```

### 6.5 接受变更

接受前至少确认：

- 文件范围符合预期；
- 当前验证已通过；
- 没有关键 `stale` 结果被误当成当前结果；
- 交付摘要与任务目标一致。

点击“接受变更”后，Runtime 会再次校验 Candidate、交付契约、Evidence 和工作区摘要，再把结果结算为“已接受”。Conversation 随后回到可继续使用的待命状态。

### 6.6 回退本轮

“回退本轮”会恢复 Run 基线，而不是盲目覆盖当前文件。

回退前 Runtime 会重新校验当前文件哈希：

- 哈希匹配：按字节恢复，并结算为“已回退”；
- 哈希不匹配：以冲突失败关闭，不覆盖用户或其他进程在此后的修改。

因此，如果回退提示 `rollback_conflict`，先查看冲突文件并决定保留哪一份内容，不要绕过校验强制覆盖。

### 6.7 继续下一轮

一轮接受、回答或回退后，直接在原 Conversation 的 Composer 中输入新要求。这样可以继续复用：

- Conversation 历史；
- 主要 Agent 的逻辑 Session；
- 已确认的上下文；
- 旧 Run 和 Review 历史。

不需要为同一个目标的每次小调整都创建新 Conversation。

## 7. 多 Agent 编排

### 7.1 工作方式

编排模式的典型流程：

```text
目标冻结
  → 编排者提出 DAG
  → 用户确认计划
  → Runtime 调度就绪节点
  → 各写任务进入隔离 worktree
  → 按拓扑和稳定 ID 合并
  → 统一验证与评审
```

最多四路并行调度。每个 Assignment 会显示等待、运行、阻塞、完成、重试或重分配状态。

### 7.2 何时介入

需要你介入的常见情况：

- 计划扩大了目标、范围、权限或预算；
- 某个 Assignment 连续恢复失败并进入阻塞；
- 合并时发现目标文件偏离节点基线；
- Strict 门禁需要人工确认；
- 交付验证缺少 Reviewer 或 Evidence。

### 7.3 合并安全

并行写 Assignment 使用独立 worktree。Runtime 按 DAG 拓扑顺序合并；如果当前文件哈希与该节点基线不一致，会拒绝覆盖并创建冲突解决任务。

子 Agent 不会直接把变化写回用户项目。只有通过最终门禁并经用户接受的集成结果，才会完成最终写回。

## 8. 状态速查

### 8.1 Conversation 状态

| 界面状态 | 含义 |
|---|---|
| 待命 | 当前没有未结算 Run，可以继续新一轮 |
| 需要澄清 | 需求仍不完整 |
| 需要你确认 | 有问题、计划或门禁等待处理 |
| 进行中 | Agent 正在执行 |
| 验证中 | Runtime 正在对账或验证 |
| 恢复中 | 正在恢复 Session 或 Run |
| 可评审 / 等待验收 | 已形成待处理 Candidate |
| 已回答 | 本轮以无文件变化的回答结算 |
| 已交付 | v1 兼容接口中的历史表达；v2 通常显示为待命 |
| 已关闭 | Conversation 终态 |

### 8.2 Assignment 状态

| 状态 | 含义 |
|---|---|
| 排队中 / 就绪 | 等待依赖或调度 |
| 运行中 | Agent 正在执行 |
| 等待输入 | 需要用户或其他 Agent 信息 |
| 已回报 | Agent 已提交结构化结果 |
| 待合并 / 合并中 | 正在进入集成工作树 |
| 已完成 | Assignment 已完成 |
| 受阻 | 无法在安全恢复额度内继续 |
| 已取消 / 失败 | 已终止或执行失败 |

## 9. 高级能力

以下能力已经有受控 API，但尚未全部作为主工作台按钮开放。除非你在做集成或自动化，否则优先使用 Conversation 界面。

### 9.1 Conversation Snapshot

```http
GET /api/v2/projects/{project_id}/conversations/{conversation_id}/snapshot?after=0&limit=200
```

该接口返回最近的活动窗口、当前 Run、Assignment、Agent Session、工具摘要和可用动作。此前的无项目作用域快照接口已删除，不提供兼容别名。

### 9.2 Replay

```http
GET /api/v2/projects/{project_id}/conversations/{conversation_id}/replay?depth=recap
```

深度：

- `recap`：关键节点摘要；
- `explore`：较完整活动；
- `verify`：只返回已验证活动。

Replay 完全来自活动账本和制品引用，并返回哈希链校验结果。

### 9.3 Preview

Preview 只接受带明确端口的 loopback URL，例如：

```text
http://127.0.0.1:3000
```

登记时还会校验进程 ID、当前 Session Generation、工作目录和 Run 归属。它不会代理任意远程 URL。

### 9.4 Memory

Run 结算后，系统会生成不可变的 Conversation Memory Checkpoint；未压缩活动超过约 8,000 token 时也会提前生成。检查点保留目标、范围、人工决定、Assignment 结果、文件/制品引用、验证状态、未解决问题、来源事件序列和哈希。

所有协作 Agent 都先注入同一份共享检查点，再叠加自己的 transcript、Assignment 和依赖输出。原始活动、终端 transcript 和旧检查点不会被删除。用户纠正会创建新版本，不覆盖历史。

评审反馈形成的 Memory Candidate 在人工批准后会成为 Rule 草稿并进入全局 Rule 库；系统不会自动覆盖 `MUXDEV.md`、`AGENTS.md` 或 `CLAUDE.md`。已有 `MUXDEV.md` 只作为 legacy advisory guidance 注入。

## 10. 常用 CLI

### 10.1 系统与 Agent 检查

```powershell
muxdev doctor
muxdev agent list
muxdev agent doctor --agent-id codex
```

### 10.2 启动 Web 控制面

```powershell
muxdev serve
muxdev serve --workspace D:\path\to\project --port 8765
```

项目显式管理：

```powershell
muxdev project add D:\path\to\project
muxdev project list
muxdev project open <project_id>
muxdev project remove <project_id>
```

`project remove` 只删除 Workbench 登记；有活跃 Agent Session 时会拒绝操作，且任何情况下都不删除项目文件和 `.muxdev` 数据。

### 10.3 兼容的单 Run 命令

这些命令主要用于旧工作流、脚本或诊断；新交互任务优先使用 Conversation：

```powershell
muxdev run "修复登录刷新问题" --workflow change --profile standard
muxdev list --limit 20
muxdev show <run_id>
muxdev resume <run_id>
muxdev cancel <run_id>
```

### 10.4 Evidence

```powershell
muxdev evidence show <run_id>
muxdev evidence verify <run_id>
muxdev evidence export <run_id>
```

具体参数以本机版本为准：

```powershell
muxdev evidence --help
muxdev evidence verify --help
```

### 10.5 Agent 在任务中的协作命令

这些命令通常由 Agent 自己在受限任务上下文中使用：

```text
muxdev collab roster
muxdev collab clarify --file assessment.json
muxdev collab ready --file requirements.json
muxdev collab plan propose --file plan.json
muxdev collab dispatch --file assignment.json
muxdev collab send --to <agent-or-assignment> "message"
muxdev collab ask --to <agent> "question"
muxdev collab report --file report.json
muxdev collab deliver --manifest delivery.json
```

任务令牌只允许访问自己的 Conversation 和权限边界，不能替用户接受最终交付。

## 11. 远程访问与安全

默认只监听 `127.0.0.1`。不要为了方便直接把无认证的本地服务暴露到公网。

远程访问应使用自管 HTTPS 反向代理或 VPN：

```powershell
muxdev serve `
  --host 0.0.0.0 `
  --allow-remote `
  --trusted-origin https://muxdev.example.com
```

远程模式会要求设备配对，并校验：

- 设备 Cookie；
- HTTP Origin；
- Session 归属；
- WebSocket 帧大小和速率；
- 文件路径是否位于授权工作区。

muxdev 不提供云中继，也不会主动上传代码、终端 transcript 或 Evidence。

## 12. 常见问题与排错

### 12.1 创建失败：`[WinError 2] 系统找不到指定的文件`

这通常表示所选 Agent 的 CLI 不在当前服务进程的 `PATH` 中。

按顺序检查：

```powershell
muxdev agent list
muxdev agent doctor --agent-id <agent_id>
```

然后：

1. 在新建任务窗口选择显示为可用的 Agent；
2. 安装缺失的 Coding CLI，或修正项目 `.muxdev/config.yaml`；
3. 在启动 `muxdev serve` 的同一终端中确认 CLI 命令可执行；
4. 重启服务，使它读取新的 PATH 和配置。

Windows 下通过 npm 安装的 `*.cmd` 启动器会由 muxdev 自动处理，不需要把 Web 配置改成 shell 字符串。

### 12.2 新建任务按钮不可用

检查：

- 任务目标不是空白；
- 已选择一个可用 Agent；
- 编排模式下 Agent 具备 `orchestrate` 能力；
- Agent Doctor 没有报告可执行文件缺失。

### 12.3 Agent 一直没有开始执行

查看 Conversation 是否处于：

- “需要澄清”：先回答中间的问题；
- “需要你确认”：处理计划或门禁；
- “受阻”：查看时间线中的原因；
- “排队中”：多 Agent 节点可能在等待依赖。

同时运行：

```powershell
muxdev doctor
muxdev agent doctor --agent-id <agent_id>
```

### 12.4 Terminal 显示只读

另一个浏览器或设备持有写入租约。关闭旧连接，或在支持接管的入口明确转移租约。不要同时从多个设备向同一 CLI 写入。

### 12.5 Terminal 无法 resize、恢复或打开

如果 Agent 显示 `pty_unavailable`，说明当前 Python 环境没有可用 ConPTY。Codex、Claude Code 等交互 CLI 不会启动，也不会创建半完成 Conversation。请在运行 daemon 的同一 Python 环境执行：

```powershell
python -m pip install "pywinpty>=3"
python -c "from winpty import PtyProcess; print('ConPTY ready')"
```

然后重启服务并运行 Agent Doctor。

如果页面显示 `session_restart_required`，说明 Session 已失败。消息草稿不会被清空，也不会产生虚假的 `user.message`。先查看只读失败输出，再点击“重新启动”；`resumable` 状态则会在打开终端或发送消息时自动恢复。

### 12.6 Changes 没有文件

先判断本轮是否是“分析回答”。如果预期应有文件变化，再检查：

- Run 是否仍在执行或验证；
- Agent 是否只输出了建议而没有写文件；
- 变更是否在允许的工作树中；
- 时间线是否有 `workspace.reconciled`；
- Review 是否显示失败或受阻。

### 12.7 验证通过后又显示 stale

验证完成后文件又发生了变化。`stale` 记录保留为历史，但不能证明当前内容。等待或触发针对最新内容的新验证。

### 12.8 请求修改失败

常见原因：

- 当前没有待评审 Run；
- 主要 Agent 已变为不可用；
- 引用的文件路径越过工作区边界；
- 当前 Candidate 已接受、回退或失效。

重新打开 Conversation 确认状态，并运行对应 Agent Doctor。

### 12.9 回退失败并提示冲突

本轮验证之后，目标文件被用户、编辑器或其他进程继续修改。muxdev 会失败关闭以保护新内容。请手动比较：

- Run baseline；
- 本轮 current；
- 工作区当前文件。

确认保留策略后，再发起一个明确的修复任务。

### 12.10 页面断线或活动停止刷新

1. 点击 Conversation 顶部刷新按钮；
2. 确认 `muxdev serve` 仍在运行；
3. 检查反向代理是否允许 SSE 和 WebSocket；
4. 远程模式检查 trusted origin 与当前访问域名是否一致；
5. 重新打开页面。活动流会使用序列游标恢复，重复事件会幂等处理。

### 12.11 端口被占用

如果端口上已经是当前用户的 Muxdev Workbench，`serve` 会健康检查实例 ID、复用它的实际端口并输出项目链接。如果端口由其他进程占用，命令会明确报错，不会误认为 Daemon 已运行。首次启动可改用其他端口：

```powershell
muxdev serve --port 8766
```

### 12.12 依赖警告或版本冲突

如果启动时出现 Python 包版本警告，先确认使用了预期虚拟环境：

```powershell
python -c "import sys; print(sys.executable)"
python -m pip check
```

不要直接删除工作区的 `.muxdev` 数据目录。它包含数据库、活动账本、Blob 和终端记录；需要迁移或恢复时先备份。

## 13. 使用建议

- 一个长期目标使用一个 Conversation，小幅返工使用“请求修改”；
- 不相关的目标创建不同 Conversation，避免上下文和交付标准互相污染；
- 任务中明确允许修改的范围和必须通过的检查；
- 低风险探索可用 Lite，普通开发默认 Standard，高风险变更使用 Strict；
- 评审顺序建议为：Summary → Changes → Review → Accept；
- 不把终端输出或 Agent 自述当成最终证据；
- 回退冲突时保留现场，先比较再决定；
- 多 Agent 模式只用于确实可拆分的任务，单一小修改优先直达模式。

## 14. 一份可复用的任务模板

```text
目标：
<一句话说明要解决的问题>

期望结果：
1. <可观察结果一>
2. <可观察结果二>

允许修改：
- <目录或模块>

禁止修改：
- <目录、接口或行为>

验收条件：
- <要运行的现有测试或行为检查>
- <兼容性、安全或性能要求>

交付内容：
- <代码变更 / 文件 / 报告 / 可运行结果 / 分析回答>
```

## 15. 相关文档

- [中文设计与运维指南](README.md)
- [English architecture and operations guide](../en/README.md)
- [项目 README](../../README.md)
