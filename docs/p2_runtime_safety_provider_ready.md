# P2 Runtime, Safety, and Provider Stability Ready Guide

本文用于验收 muxdev P2 能力：在 P0 自动/角色/设计/记忆骨架和 P1 可信交付闭环之上，补齐运行时稳定性、安全最小权限和 provider 稳定性闭环。

## 验收目标

P2 ready 的判断标准是以下链路可以端到端运行、可观测、可恢复：

- Provider stage 每次执行都会写入 `provider_attempts`，记录 provider、role、attempt、状态、失败类型、return code、artifact 和 capsule 路径。
- Session backend 能力面包含 headless、pty、tmux、docker、conpty；默认执行仍走 headless，交互/handoff 通过 attach/capsule 承接。
- provider 输出中的 CLI confirmation、auth required、rate limit、idle timeout 会进入 `provider_actions`，不会伪装成 muxdev approval。
- provider action 或 provider failure 会生成 `capsules/<stage>.session_capsule.json` 和 `capsules/<stage>.handoff.patch`，用于 attach/handoff/审计。
- `read_only: true` stage 会在执行后校验 worktree diff hash；只读角色改动文件时立即 blocked，并写入 evidence、error、attempt、capsule。
- transient provider exit 会在同一 stage 内进行有限重试，并在 trace 中记录 `provider_retry_scheduled`。
- provider score 可以通过 daemon API 和 CLI 查看，用历史 attempt/action 计算 success rate、retry rate、human intervention rate 和 score。
- Adaptive Role Router 会记录 `provider_route_decision`；只有存在足够历史且明显优于 fallback provider 时才自动切换，否则保守使用 fallback。

## 新增运行证据

一次 P2 run 可能产生这些新增记录：

```text
.muxdev/runs/<run_id>/
  capsules/
    <stage>.session_capsule.json
    <stage>.handoff.patch
  blackboard.sqlite
  trace.jsonl
```

黑板新增/强化表：

```text
provider_attempts
session_capsules
provider_actions
```

Dashboard run page 也应展示：

- Provider Actions
- Provider Attempts
- Session Capsules

## 快速验收

建议在干净测试目录中使用内置 `mock` provider：

```powershell
python -m pip install -e ".[test]"
muxdev setup --project --yes
muxdev start
```

### 1. 标准任务不应被 P2 防线误伤

```powershell
muxdev dev "p2 standard smoke" --provider mock --gate auto --json
muxdev status latest --json
muxdev evidence verify latest --json
```

通过标准：

- run status 为 `completed`。
- `provider_attempts` 至少包含 plan/code/test/review 等 stage 的 successful attempt。
- `provider_actions` 没有 pending 项。
- `muxdev evidence verify latest --json` 返回 `valid = true`。

### 2. 查看 provider score

```powershell
muxdev provider score --json
muxdev provider score --role code --json
```

通过标准：

- 返回 provider/role/score/attempts/successes/failures/retries/human_actions。
- mock smoke 后至少能看到 `mock` 的成功 attempt。

也可以直接检查 daemon API：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8788/api/provider-scores
```

### 3. Provider Action handoff

当真实 Codex/Claude/Gemini 等 CLI 输出需要登录、确认、限流或 idle timeout 时：

```powershell
muxdev actions --status pending --json
muxdev status latest --json
```

通过标准：

- run status 为 `awaiting_provider_action`。
- `muxdev actions` 展示 kind、prompt、options、transcript/chunks 路径和 attach command。
- run 目录存在 `capsules/<stage>.session_capsule.json`。
- 处理对应 CLI session 后，执行：

```powershell
muxdev action handled <action_id>
muxdev continue latest --json
```

### 4. Read-only stage 安全拦截

自动化测试覆盖了一个只读 role 写文件的场景：

```powershell
python -B -m pytest tests\test_p2_runtime_safety_provider.py::test_read_only_stage_write_violation_blocks_and_writes_capsule -q
```

通过标准：

- run status 为 `blocked`。
- `error_details.type = read_only_write_violation`。
- `provider_attempts.status = read_only_violation`。
- `session_capsules.kind = read_only_write_violation`。

### 5. Transient provider retry

自动化测试覆盖 provider 第一次 transient exit、第二次成功的场景：

```powershell
python -B -m pytest tests\test_p2_runtime_safety_provider.py::test_transient_provider_exit_retries_and_records_attempts -q
```

通过标准：

- run status 为 `completed`。
- provider 调用次数为 2。
- attempts 状态为 `retried` 后接 `succeeded`。
- `trace.jsonl` 包含 `provider_retry_scheduled`。

## 自动化验收

P2 专项：

```powershell
python -B -m pytest tests\test_p2_runtime_safety_provider.py -q
```

受影响回归：

```powershell
python -B -m pytest tests\test_runtime_m1_m4.py tests\test_stream_workflow_safety.py tests\test_daemon_client_server.py -q
```

全量验收：

```powershell
python -B -m pytest -q
```

Windows 环境中 pytest cache 写入 warning 不影响功能验收。

## 故障定位

- 标准 mock run blocked：先看 `error_details`，确认是否为 `read_only_write_violation`、`provider_exit` 或 validator reject。
- provider action 一直 pending：执行 `muxdev actions --status pending --json`，按 `attach_command` 进入对应 CLI/session 处理，再标记 handled。
- 重复 continue 没有继续执行：如果存在 pending provider action，daemon 会直接返回 `awaiting_provider_action`，这是预期保护。
- provider score 为空：需要至少有一次 daemon run 或测试手动写入 attempt；旧 daemon 需要重启后才有 `/api/provider-scores`。
- read-only 误报：比较 `snapshots/<stage>.snapshot.json` 中的 `diff_hash` 与当前 worktree diff，确认 provider 是否真的改动了文件。

## Ready 判定

当以下命令稳定通过时，P2 可以判定为 ready：

```powershell
muxdev dev "p2 runtime safety provider smoke" --provider mock --gate auto --json
muxdev evidence verify latest --json
muxdev provider score --json
python -B -m pytest tests\test_p2_runtime_safety_provider.py -q
python -B -m pytest -q
```

P2 ready 后，后续 P3 可以继续扩展 Feedback Router、CI Rescue、CAS Cache、Skill Lock、MCP Guardrail Server 和 Dashboard timeline/evidence/memory/session 的更完整展示。
