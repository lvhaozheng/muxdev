# P3 Ecosystem and Automation Ready Guide

本文用于验收 muxdev P3 能力：把生态与自动化层打通，让外部反馈、CI 救援、CAS 缓存、Skill Lock/Skill Memory、MCP Guardrail、Safe Plugin Manifest 和 Dashboard 可见性形成最小闭环。

## 验收目标

P3 ready 的判断标准是以下能力可以端到端运行并留下证据：

- Feedback Router 可把 `ci_failed`、`local_test_failure`、`review_comment`、`github_pr_comment`、`issue_comment`、`manual_feedback`、`security_blocker` 统一记录为 `feedback_events`。
- CI Rescue 可从 CI 失败反馈自动创建 rescue task，并在 `ci_rescues` 中绑定 feedback 与 rescue run。
- CAS Cache 会为反馈事件写入 `.muxdev/cache/cas/...`，并在 `cache_entries` 中记录 cache key、value hash 和路径。
- `muxdev skill lock` 会生成 `.muxdev/skill-lock.json`，记录 skill hash、version、compatible roles，并可生成 `skill_memory` proposals。
- Safe Plugin Manifest 会在 `muxdev plugin validate/add` 时解析 `.codex-plugin/plugin.json` 或 `plugin.json`，对 shell/network/secrets 等敏感权限标记 `needs_review`。
- MCP Guardrail Server 暴露 `muxdev.check_policy`、`muxdev.ask_approval`、`muxdev.write_event`、`muxdev.register_artifact`、`muxdev.read_blackboard`、`muxdev.query_memory`、`muxdev.verify_patch`、`muxdev.get_acceptance_criteria`。
- Dashboard task detail 展示 timeline、evidence、memory context、role sessions、feedback、CI rescue、CAS cache、skill lock、plugin manifest 和 guardrail events。

## 新增持久化表

P3 新增或强化以下黑板表：

```text
feedback_events
ci_rescues
cache_entries
skill_locks
plugin_manifests
guardrail_events
```

本地非 daemon 命令会写入 `.muxdev/ecosystem.sqlite`；daemon feedback/API 会写入 daemon DB。

## 快速验收

建议使用内置 `mock` provider：

```powershell
python -m pip install -e ".[test]"
muxdev setup --project --yes
muxdev serve --restart
```

### 1. Feedback Router + CI Rescue

```powershell
muxdev feedback add ci_failed "pytest failed in tests/test_login.py" --source github-actions --provider mock --json
muxdev feedback list --json
muxdev cache list --json
```

也可以使用 CI 专用入口：

```powershell
muxdev ci rescue "npm test failed on auth flow" --source github-actions --provider mock --json
```

通过标准：

- 返回 payload 中包含 `feedback_id`、`route_to = test`、`auto = true`。
- 自动提交 rescue task，`submitted.run_id` 存在。
- `/api/ecosystem` 中能看到 `feedback_events`、`ci_rescues`、`cache_entries`。

### 2. Skill Lock + Skill Memory

```powershell
muxdev skill lock --json
muxdev memory query "" --status proposed --json
```

通过标准：

- `.muxdev/skill-lock.json` 存在。
- 每个 skill 包含 `skill_hash`、`version`、`compatible_roles`、`path`。
- 默认会生成 `kind = skill_memory` 的 memory proposals。
- 若只想生成 lock 不写 memory，可执行 `muxdev skill lock --no-memory --json`。

### 3. Safe Plugin Manifest

```powershell
muxdev plugin validate path\to\plugin --json
muxdev plugin add path\to\plugin --json
```

通过标准：

- manifest hash 被记录。
- 只有 read/write_workspace/mcp/skill/dashboard 等安全权限时可为 `trusted`。
- 包含 `shell`、`network`、`secrets`、`write_home`、`write_root` 等敏感权限时状态为 `needs_review`，并包含 warnings。
- 本地 `.muxdev/ecosystem.sqlite` 的 `plugin_manifests` 有对应记录。

### 4. MCP Guardrail Server

检查 manifest：

```powershell
muxdev mcp manifest --json
```

单次调用 policy check：

```powershell
muxdev mcp serve --request '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"muxdev.check_policy","arguments":{"command":"rm -rf /"}}}'
```

通过标准：

- manifest 中包含 `muxdev.check_policy` 等 guardrail tools。
- 危险命令返回 `decision = deny`。
- `.muxdev/ecosystem.sqlite` 中存在 `guardrail_events` 记录。

### 5. Dashboard 可见性

启动 daemon 后打开：

```powershell
muxdev dashboard
```

通过标准：

- Task detail 显示 Timeline、Evidence、Memory Context、Role Sessions。
- 触发 feedback/CI rescue 后，可以在 detail 或 `/api/ecosystem` 看到 Feedback / CI Rescue / CAS Cache。
- 有 provider action/capsule 时，Role Sessions 能看到 session capsule 路径。

## 自动化验收

P3 专项：

```powershell
python -B -m pytest tests\test_p3_ecosystem_automation.py -q
```

受影响回归：

```powershell
python -B -m pytest tests\test_daemon_client_server.py tests\test_cli.py tests\test_structure.py -q
```

全量验收：

```powershell
python -B -m pytest -q
```

Windows 环境中 pytest cache 写入 warning 不影响功能验收。

## 故障定位

- `feedback add` 返回 404：旧 daemon 未重启，执行 `muxdev serve --restart`。
- CI rescue 未自动提交：确认 kind 是 `ci_failed` 或 `local_test_failure`，且未传 `--no-auto-submit`。
- `cache list` 为空：先触发一次 feedback/CI rescue；CAS cache 当前最小闭环记录 feedback event。
- `skill lock` 生成太多 memory proposals：使用 `--no-memory`，或后续用 `muxdev memory quarantine <mem_id>` 清理不需要的项目记忆。
- plugin 一直 `needs_review`：检查 manifest permissions，敏感权限需要人工信任，P3 不会执行插件代码。
- MCP guardrail 写不进 run：`muxdev.ask_approval`、`register_artifact` 需要有效 `run_id`；无 run_id 的 event 会写入 `.muxdev/ecosystem.sqlite`。

## Ready 判定

当以下命令稳定通过时，P3 可以判定为 ready：

```powershell
muxdev feedback add ci_failed "pytest failed" --source ci --provider mock --json
muxdev cache list --json
muxdev skill lock --no-memory --json
muxdev plugin validate path\to\plugin --json
muxdev mcp manifest --json
python -B -m pytest tests\test_p3_ecosystem_automation.py -q
python -B -m pytest -q
```

P3 ready 后，后续 P4 可以继续扩展 conflict-aware parallel squad、semantic merge reviewer、cross-run provider learning、memory contradiction detection、memory quarantine automation 和 multi-repo orchestration。
