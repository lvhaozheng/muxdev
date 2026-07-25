# muxdev 系统体验审计与修复报告

审计日期：2026-07-24  
审计对象：当前工作区实现、生产静态构建，以及 `D:\jianzhi\demo\721` 的实际本地服务  
浏览器矩阵：Microsoft Edge / Playwright，1440×900、1024×768、390×844

## 结论

截图中的 `创建失败：[WinError 2]` 已修复。根因是两层问题叠加：

1. 新建任务默认选中了排序第一、但本机未安装 CLI 的 `claude-code`。
2. Windows 上即使 `codex` 可被 `PATH` 找到，实际命中的是 `codex.cmd`；此前仍以裸命令交给 `subprocess.Popen(shell=False)`，可能触发同一类 WinError 2。

修复后，前端只会自动选择可用且满足当前协作方式的 Agent；不可用 Agent 会被禁用并解释原因。后端在创建任何持久化 Conversation 前再次做可用性预检，并将 `.cmd/.bat/.ps1` 转换为 Windows 可执行调用。

本轮深度体验未发现仍未解决的 P0、P1 或 P2 问题。全量回归结果为 `148 passed, 4 skipped`，浏览器回归为 `7 passed`。

## 已定位并修复的问题

| 严重度 | 问题 | 定位 | 修复 |
|---|---|---|---|
| P0 | 新建任务触发 WinError 2 | 默认选择未安装的 Claude CLI；Windows `.cmd` 未包装 | 前后端双重可用性校验；Windows script invocation |
| P1 | 启动失败后留下半成品 Conversation | 创建状态先落库，进程启动后失败 | 在持久化之前 `require_available`，失败返回可行动中文提示 |
| P1 | Request Changes 创建临时写入 lane | 内部使用 `dispatch_kind=write` | 改为下一轮 `direct` Run，复用同一主 Agent Session |
| P1 | Request Changes 路由失败会先废弃旧候选 | 旧 Run/Candidate 在新任务启动前结算 | 先完成可用性预检与新轮路由，成功后再 supersede |
| P1 | 安全回退没有进入活动账本 | discard 只改状态和文件 | 追加 verified `workspace.rolled_back` 事件 |
| P1 | 回退/接受后的 Review 结果仍显示“文件变化” | outcome 只识别 answered | 增加 accepted、rolled_back、superseded 的明确结果 |
| P1 | 切换 Conversation 后残留上一 Conversation 的文件选择 | Tool Canvas 本地状态未按 Conversation/Run 重置 | 切换时重置 tab、文件、视图和反馈草稿 |
| P1 | Conversation 加载期间误显示“创建第一个任务” | 列表先于 Conversation 快照返回 | 增加明确的 Conversation 恢复加载态 |
| P2 | 修改请求失败时反馈草稿被清空 | 点击后立即清空 textarea | 仅在 mutation 成功后清空，失败保留以便重试 |
| P2 | 新建弹窗 Esc 关闭后焦点未回到触发按钮 | 焦点在 autofocus 后才被记录 | 打开前由 App 保存触发焦点并在关闭时恢复 |
| P2 | 弹窗缺少完整键盘闭环 | 无 Escape、Tab trap、表单复位 | 增加 Escape、焦点圈定、焦点恢复和关闭复位 |
| P2 | 错误 Toast 对读屏器仍是普通状态 | 所有 Toast 都使用 `role=status` | 错误改为 `role=alert` 并使用危险色 |
| P2 | 时间线暴露 `queued`、`run.event` 等内部枚举 | 事件 fallback 直接输出内部类型与 payload | 补全用户语言映射，未知事件降级为“系统活动” |
| P2 | Header 中存在无行为的通知/更多/移动返回按钮 | 视觉控件没有交互实现 | 移除死控件，只保留真实可用操作 |
| P2 | E2E 复用旧数据库并可能积累重名会话 | 固定 browser-e2e 工作区 | 每次使用独立进程工作区并生成真实 Evidence v3 候选 |

## 逐步体验结果

1. Session Rail 与 Conversation 首屏：健康  
   截图：`.test_workspaces/audit-current/01-session-rail-and-conversation.png`  
   验证分组、选中态、Timeline、Fleet、Summary 和 Action Bar。

2. 新建任务与 Agent 可用性：健康  
   截图：`.test_workspaces/audit-current/02-create-dialog-agent-availability.png`  
   验证不可用 Agent 禁用、可用 Agent 自动选择、Escape、Tab、焦点恢复和提交约束。

3. 创建任务与逻辑 Agent Terminal：健康  
   截图：`.test_workspaces/audit-current/03-created-conversation-terminal.png`  
   验证新任务创建、活动增量、Session 终端连接和租约状态。

4. 手机端澄清与键盘提交：健康  
   截图：`.test_workspaces/audit-current/04-mobile-clarification.png`  
   验证 390×844 无横向溢出、Interaction 回答、Enter 提交、澄清卡消失和同 Session 续跑。

5. 平板端 Changes：健康  
   截图：`.test_workspaces/audit-current/05-tablet-changes.png`  
   验证 1024×768 三栏压缩、文件选择、Diff、统计和底部评审操作。

6. 桌面端 Changes：健康  
   截图：`.test_workspaces/audit-current/06-desktop-changes.png`  
   验证 1440×900 详情密度、无控制台错误、无失败响应、无横向溢出。

7. Review 与 Request Changes：健康  
   截图：`.test_workspaces/audit-current/07-review-request-changes.png`  
   验证验证历史、文件引用、反馈提交、旧结果 superseded、下一轮 Run 和主 Session 复用。

8. Accept：健康  
   截图：`.test_workspaces/audit-current/08-review-before-accept.png`  
   使用真实 Evidence v3 候选验证接受门禁、状态回到 Idle 和评审结算。

9. Discard/Rollback：健康  
   截图：`.test_workspaces/audit-current/09-rollback-timeline.png`  
   验证当前哈希检查、字节级恢复、Idle 结算和 verified 账本事件。

10. 非视觉接口：健康  
    覆盖 SSE 游标恢复、10,000 条事件窗口与性能门槛、路径穿越、Preview 非 loopback 拒绝、Replay 证据等级、Memory 人工批准、设备撤销、Origin、WebSocket 租约和接管。

## 验证记录

- `pytest -q`：`148 passed, 4 skipped in 326.34s`
- `npx playwright test`：`7 passed`
- `npx tsc --noEmit -p web/tsconfig.json`：通过
- `npm run build:web`：通过，生产静态产物已更新
- `python -m compileall -q src/muxdev scripts/e2e_server.py`：通过
- `git diff --check`：通过；仅有仓库既有的 Windows LF/CRLF 提示
- 实际服务：`D:\jianzhi\demo\721` 已用本轮代码重启，`/health` 返回 200；Claude 显示不可用，Codex 正确解析为 `codex.cmd`
- 实际创建失败回归：选择不可用 Claude 返回 422；Conversation 数量在请求前后均为 6，没有留下脏记录

## 可访问性说明

本轮自动验证了语义化控件、可见焦点、Escape、Tab 焦点圈定、焦点恢复、键盘提交、错误 `role=alert`、系统深浅色和 reduced-motion。尚未由真实屏幕阅读器用户或运动/视觉障碍用户完成手工测试，因此不宣称达到某一正式 WCAG 合规等级。

## 剩余非阻断项

- Python 环境持续输出 `requests` 与 `urllib3/chardet` 版本兼容警告；它没有影响本轮功能或测试，但建议后续统一环境依赖版本。
- Windows 当前缺少 ConPTY Python 后端，因此 Agent Terminal 明确降级为 pipe，交互 resize/process resume 不会被错误宣传为可用。
