# Runtime Safety And Provider Stability Readiness

本指南用于验收 muxdev 的运行时稳定性、安全最小权限和 provider handoff 闭环。

## 验收目标

- 每次 provider stage 执行都写入 `provider_attempts`。
- session backend 支持 headless、pty、tmux、docker、conpty 等能力面；默认仍走 headless。
- provider CLI confirmation、auth required、rate limit、idle timeout 进入 `provider_actions`。
- Provider Action 不会被伪装成 muxdev approval。
- provider action 或 provider failure 会生成 `session_capsule` 和 handoff patch。
- `read_only: true` stage 执行后校验 worktree diff；只读角色写文件会被 blocked。
- transient provider exit 会有限重试，并在 trace 中记录。
- provider score 可从 CLI 和 daemon API 查询。

## 运行证据

```text
.muxdev/runs/<run_id>/
  capsules/
    <stage>.session_capsule.json
    <stage>.handoff.patch
  blackboard.sqlite
  trace.jsonl
```

关键 blackboard 表：

```text
provider_attempts
session_capsules
provider_actions
```

## 快速验收

```powershell
python -m pip install -e ".[test]"
muxdev setup --project --yes
muxdev serve --restart
```

### 标准任务不应被安全防线误伤

```powershell
muxdev dev "runtime safety provider smoke" --provider mock --gate auto --json
muxdev status latest --json
muxdev evidence verify latest --json
```

通过标准：

- run status 为 `completed`。
- `provider_attempts` 至少包含 plan/code/test/review 等 stage 的 successful attempt。
- 没有 pending provider action。
- Evidence v2 校验通过。

### Provider score

```powershell
muxdev provider score --json
muxdev provider score --role code --json
```

通过标准：

- 返回 provider、role、score、attempts、successes、failures、retries、human_actions。
- mock smoke 后至少能看到 `mock` 的成功 attempt。

也可以检查 API：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8788/api/provider-scores
```

### Provider Action handoff

当真实 provider CLI 需要登录、确认、限流恢复或处理卡住的 session：

```powershell
muxdev actions --status pending --json
muxdev status latest --json
```

通过标准：

- run status 为 `awaiting_provider_action`。
- action 包含 kind、prompt、options、transcript/chunks 路径和 attach command。
- 用户处理 provider session 后执行：

```powershell
muxdev action handled <action_id>
muxdev continue latest --json
```

### 只读 stage 拦截

```powershell
python -B -m pytest -q -k "read_only_stage"
```

通过标准：

- run status 为 `blocked`。
- `error_details.type = read_only_write_violation`。
- `provider_attempts.status = read_only_violation`。
- `session_capsules.kind = read_only_write_violation`。

## 回归

```powershell
python -B -m pytest -q -k "read_only_stage or transient_provider_exit or provider_action_writes or provider_score_api"
python -B -m pytest -q
```
