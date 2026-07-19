# 运维

[English](../en/operations.md)

<!-- section:daemon -->
## Daemon 生命周期

本地 daemon 拥有任务写入权和有界 worker pool。使用 `muxdev start`、`muxdev serve --status`、`muxdev serve --restart`；升级后运行 `muxdev doctor`。除非另外建立并审查认证边界，否则 API 与 Dashboard 必须绑定 loopback。重启 daemon 不应要求删除 run 或 SQLite。

<!-- section:task -->
## 观察与控制任务

使用 `muxdev tasks`、`status`、`story`、`diff`、`report`、`evidence`、`approvals`、`actions`。自动化应优先使用 JSON 输出。处于 `awaiting_approval`、`awaiting_provider_action`、`awaiting_feedback`、`paused_budget` 或 `blocked` 的任务是在按设计等待；应查看 next action，而不是反复提交重复任务。

<!-- section:recovery -->
## 恢复与协调

先保存 status、execution attempt、最新 error、lease state 和 report。无副作用的安全工作可以在 lease 过期后重新入队；可能对外写入的不透明 attempt 必须 reconciliation。只有理解记录的 reason 后才能使用 `continue`、`recover` 或受控 rollback。Fencing 会自动拒绝旧 worker；不要通过直接改行绕过它。

<!-- section:backup -->
## 备份与恢复

通过受控 storage backup API/CLI 创建 archive，然后验证 manifest 和路径安全。只能恢复到明确且为空的 target，并在切换前校验 schema/migration checksum。v7 数据库和 run artifact 布局属于兼容边界。包含路径穿越的 archive 或来自不受支持新 schema 的数据库绝不能恢复。

<!-- section:benchmark -->
## Benchmark 与 Replay

Replay Benchmark fixture 已注册并绑定 hash，是确定性、无副作用的回归工具，必须标记为 simulation。Live Benchmark 需要显式确认、allowlist 和成本上限；只有 mode、Evidence 与完整性检查通过后，现场结果才允许进入 production learning。

<!-- section:diagnostics -->
## 诊断清单

1. 使用 `ruff check .` 和 `python scripts/verify_docs.py` 检查仓库健康。
2. 先运行故障区域的针对性测试，再运行 `pytest -q -m "not release"`。
3. 检查 daemon log、task trace、Provider transcript、契约验证和 Evidence evaluation。
4. 检查磁盘空间、Git worktree、API 端口、当前 Provider 指纹和签名 key 权限。
5. 清理前保留已验证备份和任务报告。

安全事件处理见[安全与信任](security-and-trust.md)。
