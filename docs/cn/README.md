# muxdev 中文设计与运维指南

## 1. 设计理念

muxdev 是 Coding CLI 的薄控制面，不是 Agent SDK wrapper。它不重建 Codex 或 Claude Code 的记忆、工具协议、Skill、MCP、Plan Mode 和审批；它只管理 CLI 进程之外必须由产品负责的事实：Conversation、PTY、Assignment、权限、工作树、合并、Evidence、交付候选与接受后写回。

角色提示只影响行为，不是安全边界。argv/env 白名单、Assignment 范围、worktree 隔离、确定性检查和交付 Gate 由 Runtime 强制执行。

## 2. Agent、Conversation、Assignment

- `CliAdapterDefinition` 描述完整 CLI 的启动 argv、恢复 argv、工作目录和模型参数、环境变量白名单、PTY/resize/resume 能力与 session ID 发现方式。
- `AgentDefinition` 描述显示名、CLI、模型、角色提示、能力标签、编排资格、最大并发和默认权限。
- `Conversation` 是用户可见的长期协作容器，拥有模式、主 Agent、编排者、活动计划、集成工作树和 DeliveryContract。
- `Assignment` 是最小派发和交付单位，固定状态从 `proposed` 到 `completed/blocked/failed/cancelled`。
- `AgentSession` 为 Agent/Assignment 管理独立 CLI 进程、终端、原生 session ID、transcript、写入租约和恢复方式。

Agent 只收到冻结任务契约、自己的简报、依赖节点产物和定向消息，不会自动获得完整 Conversation transcript。派发、回报、提问、合并和交付会投影到共享时间线。

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

daemon 重启后：tmux 直接 reattach；普通 PTY 使用 CLI 原生 session ID；不支持 resume 的 CLI 会用任务简报、依赖产物和 transcript 摘要启动新会话，并标记 `rebuilt_context`。

## 5. Web 终端和远程安全

Dashboard 使用本地打包的 xterm.js 与 fit addon，不依赖 CDN。WebSocket JSON 帧：

- 客户端：`attach`、`input`、`resize`、`release_write`；
- 服务端：`output`、`status`、`lease`、`error`。

终端 transcript 是带序号的本地 JSONL，权限尽量限制为所有者读写。一个 Session 只有一个有效写入租约，其他设备只读。输出断线重放使用 `after_seq`，输入帧限制 64 KiB，并有滑动窗口限流。

远程模式必须启用 HTTPS 反向代理或 VPN，并完成一次性设备配对。WebSocket 不依赖 HTTP 中间件，自己校验 Cookie、Origin、Session、帧大小和速率。

## 6. 三段式团队交付标准

`muxdev.delivery-standard.v2` 的每一项都包含：

- `deliverable`：需要交付的内容；
- `completion`：可判定的完成条件；
- `proof`：可信证明方式；
- 可选 `assignment_id`；
- `verifier`：`runtime_check`、`agent_review`、`artifact` 或 `human_acceptance`。

内置基线不可删除或降级。Runtime check 只能引用 Workflow 中冻结的 argv 命令；Agent review 必须绑定 Reviewer 身份和当前 Subject；artifact 必须内容寻址；人工接受必须是结构化事件。

标准修改会生成新 Contract、记录受影响 Assignment 并使旧 Candidate 失效。已接受的 Candidate、Contract 和 Evidence 不会被改写。

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

schema v9 打开时自动增加 v10 列和四张协作表；旧记录标记为 `legacy_pipeline`。`/api/v1`、`/runs`、ACP/MCP、headless Provider 与固定四工作流不删除，但新 Dashboard 默认使用 `/api/v2`。

## 9. 与 botmux 的关系

muxdev 参考 botmux 的薄编排和 CLI 进程思路，不复制代码。botmux 的飞书话题和群成员路由，在这里由本地 Conversation、Assignment、AgentSession 与共享时间线取代。muxdev 额外提供隔离 worktree 的 fail-closed 合并、Evidence v3、三段式交付标准和接受后冲突安全写回。
