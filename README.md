# muxdev

muxdev 是一个本地优先、Conversation 原生的多 Agent 开发与可信交付控制面。

它直接桥接完整的 Coding CLI（Codex、Claude Code 等），负责 Conversation、PTY、Agent 派发、隔离 worktree、合并、Evidence 和接受后写回；不会把 Coding CLI 重建成一层 Agent SDK wrapper。Agent 自己的记忆、工具调用、Skill、MCP、Plan Mode 与审批协议仍由原 CLI 提供。

## 两条默认路径

- `direct`：默认模式。任务发给一个主 Agent，由它端到端完成；交付标准要求时，Runtime 才追加独立 Reviewer。
- `orchestrated`：显式选择具备 `orchestrate` 能力的 Agent。编排者先提交 DAG 和分工，用户确认一次后，Runtime 最多四路并行调度、稳定顺序合并并统一验证。
- `legacy_pipeline`：保留原固定 `change / design / review / test` DAG、headless Provider、ACP/MCP 和 `/runs`，用于兼容旧 Conversation 与自动化。

多个 Agent 共享一条可审计时间线，但每个 Assignment 拥有独立 CLI 上下文、终端和工作树。普通消息只发给主 Agent；`@agent`、`consult` 或 `write` 才会显式唤起其他 Agent。只读咨询不会合并改动，写任务必须进入独立子 worktree。

## 快速开始

需要 Python 3.11+。

```powershell
python -m pip install -e ".[test]"
muxdev init
muxdev agent list
muxdev agent doctor
muxdev serve
```

打开 `http://127.0.0.1:8765` 后，可以选择“直接执行 / 编排执行”和 Agent。Dashboard 提供：

- 共享 Conversation 时间线和定向消息；
- 编排 DAG、依赖、Agent 分配与 Runtime 可证明的节点状态；
- 每个 Agent 的独立 xterm.js Web 终端；
- 手机端 Esc、Ctrl+C、Tab、方向键和收起键盘快捷栏；
- 任务契约、三段式团队交付标准和 Delivery Candidate。

Agent 也可以在自己的 CLI 中使用任务作用域控制命令：

```text
muxdev collab roster
muxdev collab plan propose --file plan.json
muxdev collab dispatch --file assignment.json
muxdev collab send --to <agent-or-assignment> "message"
muxdev collab ask --to <agent> "question"
muxdev collab report --file report.json
muxdev collab deliver --manifest delivery.json
```

这些命令使用短期、哈希存储的 Assignment 令牌，只能访问自己的 Conversation 和权限边界，不能接受最终交付。

## CLI-native Agent 配置

配置沿用“内置 → 用户 → 项目 `.muxdev/config.yaml` → `MUXDEV_CONFIG`”优先级，新增 `cli_adapters` 与 `agents`：

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

可执行命令只接受 argv 数组，不接受 shell 字符串；Web 端只能查看和选择 Agent，不能修改路径或敏感环境变量。

## Web 终端与恢复

- Windows 优先 ConPTY/`pywinpty`；Linux/macOS 使用 POSIX PTY。
- Unix 检测到 tmux 且 Adapter 启用时可直接 reattach。
- 普通 PTY 重启后优先使用 CLI 原生 session ID；不支持 resume 时，用任务简报、上游产物和 transcript 摘要重建上下文，并明确标记。
- transcript 以受限权限 JSONL 保存并带单调序号；原始终端流不会自动成为 Evidence。
- 同一终端只有一个浏览器写入租约，其他连接只读；“接管终端”会显式转移租约。

远程访问必须使用自管 HTTPS 反向代理或 VPN：

```powershell
muxdev serve --host 0.0.0.0 --allow-remote --trusted-origin https://muxdev.example.com
```

远程 WebSocket 会独立校验设备 Cookie、Origin、Session 归属、帧大小和速率。muxdev 不提供云中继，也不会主动上传代码、transcript 或 Evidence。

## 可信交付

每个任务冻结一份 `muxdev.delivery-standard.v2`：

1. `deliverable`：交付什么；
2. `completion`：怎样算完成；
3. `proof`：由什么证明。

自定义 verifier 只能引用：冻结的 Runtime check、独立 Agent review、内容寻址 artifact 或人工接受。用户文本不能注入任意 shell 命令。修改标准会创建新 DeliveryContract、记录差异并使旧候选失效；已接受交付保持不可变。

写 Assignment 完成时生成内容寻址 ChangeSet。Runtime 按拓扑和稳定 Assignment ID 顺序合并；目标文件偏离节点基线时拒绝覆盖并创建冲突解决 Assignment。全部节点完成后才在集成工作树运行确定性检查、独立评审和 Evidence v3 Gate。子 Agent 不能直接写回用户项目；用户接受候选时，Runtime 再验证 Evidence、契约版本和工作区摘要后冲突安全写回。

## 公共接口与兼容性

v2 主接口：

- `GET /api/v2/agents`
- `POST /api/v2/conversations`
- `POST /api/v2/conversations/{id}/messages`
- `POST /api/v2/conversations/{id}/orchestration/approve|revise`
- `POST /api/v2/assignments/{id}/retry|reassign|cancel`
- `WS /api/v2/sessions/{session_id}/terminal`

原 `/api/v1`、`/runs`、8 个 MCP 工具、ACP 与固定工作流继续兼容。SQLite schema v10 在原 18 张表上新增 `agent_sessions`、`orchestration_plans`、`assignments`、`assignment_dependencies`；旧 Conversation 自动迁移为 `legacy_pipeline`。

## 设计来源与差异

muxdev 参考 botmux 的薄编排、CLI 进程模型、显式多 Agent 派发和 Web 终端思想，但不复制其代码，也不依赖飞书。botmux 的群聊话题在这里由本地 Conversation、Assignment 和共享时间线替代；muxdev 的核心增量是隔离 worktree 合并、Evidence 链、交付标准以及接受后写回。

更完整说明：

- [中文设计与运维指南](docs/cn/README.md)
- [English architecture and operations guide](docs/en/README.md)

## 验证

```powershell
python -m pytest -q
npm install
npm run build:web
```

`mock` Agent 只用于离线流程、终端和门禁测试，不代表真实 Coding CLI 的能力。
