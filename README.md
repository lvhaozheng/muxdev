# muxdev

muxdev 是一个本地优先、Conversation 原生的多 Agent 开发与可信交付控制面。

它直接桥接完整的 Coding CLI（Codex、Claude Code 等），负责 Conversation、PTY、Agent 派发、隔离 worktree、合并、Evidence 和接受后写回；不会把 Coding CLI 重建成一层 Agent SDK wrapper。Agent 自己的记忆、工具调用、Skill、MCP、Plan Mode 与审批协议仍由原 CLI 提供。

## 两条默认路径

- `direct`：默认模式。任务发给一个主 Agent，由它端到端完成；交付标准要求时，Runtime 才追加独立 Reviewer。
- `orchestrated`：显式选择具备 `orchestrate` 能力的 Agent。编排者先提交 DAG 和分工，用户确认一次后，Runtime 最多四路并行调度、稳定顺序合并并统一验证。
- `legacy_pipeline`：保留原固定 `change / design / review / test` DAG、headless Provider、ACP/MCP 和 `/runs`，用于兼容旧 Conversation 与自动化。

Conversation 创建后先由主 Agent 判断需求是否完整；澄清问题和回答只记录为 Interaction，不创建 Assignment 或 Run。普通消息进入主 Agent 的 `main` Session，`@agent` 只让该 Agent 加入 Conversation；显式 `consult`、`write`、`review` 或主 Agent 的结构化派发才创建 Assignment Run。一次完整任务段对应一个 Run，重试是同一 Run 的新 Attempt；最终门禁使用独立的 `delivery_verification` Run。

Session 的逻辑键是 `Conversation × Agent × lane`。顺序任务复用 `main` lane；并发或隔离工作树不兼容时才创建临时 lane。进程重启、原生恢复和上下文重建只增加 Session Generation，逻辑 Session ID、终端入口和 transcript 序号保持不变。

## 快速开始

需要 Python 3.11+。

```powershell
python -m pip install -e ".[test]"
muxdev init
muxdev agent list
muxdev agent doctor
muxdev serve
```

`muxdev serve` 默认登记当前目录。首次执行启动用户级单例 Workbench Daemon 并保持前台运行；以后从其他项目目录再次执行时，会复用同一端口、登记该目录、输出类似 `http://127.0.0.1:8765/projects/{project_id}` 的深链并退出。Dashboard 提供：

- 可切换本地已登记项目的 Project Rail，项目数据库、SSE、文件和 Agent Session 严格隔离；
- 共享 Conversation 时间线和定向消息；
- 不可变 Conversation Memory Checkpoint 和用户纠正版本；
- 编排 DAG、依赖、Agent 分配与 Runtime 可证明的节点状态；
- 每个 Agent 的独立 xterm.js Web 终端，默认只读、显式申请写租约并可中断当前命令；
- 手机端 Esc、Ctrl+C、Tab、方向键和收起键盘快捷栏；
- 三类 Review 记录、Rule 冻结、任务契约、团队交付标准和 Delivery Candidate。

Agent 也可以在自己的 CLI 中使用任务作用域控制命令：

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

这些命令使用短期、哈希存储的 Assignment 令牌，只能访问自己的 Conversation 和权限边界，不能接受最终交付。

## CLI-native Agent 配置

配置沿用“内置 → 用户 → 项目 `.muxdev/config.yaml` → `MUXDEV_CONFIG`”优先级，新增 `cli_adapters` 与 `agents`。内置目录覆盖 Codex、Claude Code、Qwen Code、Kimi Code、Trae 和 Antigravity；打开“新建任务”时会重新按 Provider 目录中的命令及别名扫描 daemon 的 `PATH`，只允许选择实际可启动且满足 PTY 要求的主 Agent：

```yaml
cli_adapters:
  codex:
    cli_id: codex
    command: [codex, --no-alt-screen]
    resume_command: [codex, --no-alt-screen, resume, "{native_session_id}"]
    working_directory_arg: -C
    env_allowlist: [OPENAI_API_KEY, OPENAI_BASE_URL]
    supports_pty: true
    supports_resize: true
    supports_resume: true

agents:
  codex:
    agent_id: codex
    display_name: Codex
    cli_id: codex
    capability_tags: [code, review, security-review, orchestrate, terminal, resume]
    can_orchestrate: true
    max_concurrency: 4
    default_permissions: workspace-write
    enabled: true
```

可执行命令只接受 argv 数组，不接受 shell 字符串；Web 端只能查看和选择 Agent，不能修改路径或敏感环境变量。未检测到的内置 Agent 会保留在“未就绪”分组中并给出安装提示；安装 CLI 后确保 daemon 的 `PATH` 可见该命令，再次打开新建窗口即可重新检测。

## Web 终端与恢复

- Windows 优先 ConPTY/`pywinpty`；Linux/macOS 使用 POSIX PTY。Doctor、API 和界面报告实际后端；降级为 `pipe` 时禁用 resize 和进程恢复。
- Unix 检测到 tmux 且 Adapter 启用时可直接 reattach。
- 普通 PTY 重启后优先使用 CLI 原生 session ID；不支持 resume 时，用任务简报、上游产物和 transcript 摘要重建上下文，并明确标记。
- transcript 以受限权限 JSONL 保存并带单调序号；原始终端流不会自动成为 Evidence。
- 同一终端只有一个浏览器写入租约，其他连接只读；“接管终端”会显式转移租约。
- Dashboard 的设备抽屉列出已配对设备并可撤销；终端标题持续显示连接中、只读、其他设备占用、可写或已断开。

远程访问必须使用自管 HTTPS 反向代理或 VPN：

```powershell
muxdev serve --host 0.0.0.0 --allow-remote --trusted-origin https://muxdev.example.com
```

远程 WebSocket 会独立校验设备 Cookie、Origin、Session 归属、帧大小和速率。muxdev 不提供云中继，也不会主动上传代码、transcript 或 Evidence。

## 可信交付

新建任务优先选择“代码修改、指定文件、报告/文档、可运行应用/API、分析回答、其他”之一；Runtime 将简短选择编译并冻结为 `muxdev.delivery-standard.v2`：

1. `deliverable`：交付什么；
2. `completion`：怎样算完成；
3. `proof`：由什么证明。

自定义 verifier 只能引用：冻结的 Runtime check、独立 Agent review、内容寻址 artifact 或人工接受。用户文本不能注入任意 shell 命令。修改标准会创建新 DeliveryContract、记录差异并使旧候选失效；已接受交付保持不可变。

写 Assignment 完成时生成内容寻址 ChangeSet。Runtime 按拓扑和稳定 Assignment ID 顺序合并；目标文件偏离节点基线时拒绝覆盖并创建冲突解决 Assignment。全部节点完成后才在集成工作树运行确定性检查、独立评审和 Evidence v3 Gate。子 Agent 不能直接写回用户项目；用户接受候选时，Runtime 再验证 Evidence、契约版本和工作区摘要后冲突安全写回。

## 公共接口与兼容性

v2 主接口全部使用项目作用域：

- `GET/POST /api/v2/projects`
- `GET /api/v2/projects/{project_id}/snapshot`
- `GET /api/v2/projects/{project_id}/agents`
- `POST /api/v2/projects/{project_id}/conversations`
- `GET /api/v2/projects/{project_id}/conversations/{id}/snapshot`
- `POST /api/v2/projects/{project_id}/conversations/{id}/messages`
- `POST /api/v2/projects/{project_id}/conversations/{id}/review/request-changes`
- `POST /api/v2/projects/{project_id}/sessions/{session_id}/interrupt`
- `WS /api/v2/projects/{project_id}/sessions/{session_id}/terminal`
- `GET/POST /api/v2/rules`

无 `project_id` 的旧 v2 路由已经删除。原 `/api/v1`、`/runs`、8 个 MCP 工具、ACP 与固定工作流继续兼容。项目 SQLite schema v13 增加每轮结算、活动账本、ChangeService、不可变 Memory/Review 和 Rule 快照；全局 Workbench 数据单独保存在平台用户数据目录，不移动或合并项目数据库。

## 设计来源与差异

muxdev 参考 botmux 的薄编排、CLI 进程模型、显式多 Agent 派发和 Web 终端思想，但不复制其代码，也不依赖飞书。botmux 的群聊话题在这里由本地 Conversation、Assignment 和共享时间线替代；muxdev 的核心增量是隔离 worktree 合并、Evidence 链、交付标准以及接受后写回。

更完整说明：

- [中文产品使用手册](docs/cn/product-user-manual.md)
- [中文设计与运维指南](docs/cn/README.md)
- [English architecture and operations guide](docs/en/README.md)

## 验证

```powershell
python -m pytest -q
npm install
npm run build:web
npm run test:e2e
```

浏览器 E2E 在 Windows 使用本机 Edge；其他环境先执行 `npx playwright install chromium`。`mock` Agent 只用于离线流程、终端和门禁测试，不代表真实 Coding CLI 的能力。
