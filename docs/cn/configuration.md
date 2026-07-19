# 配置

[English](../en/configuration.md)

<!-- section:precedence -->
## 优先级

运行时配置以 TOML 为主：内置值依次与全局配置、项目 `.muxdev/config.toml`、可选 task file 合并，范围更具体的后置值胜出。静态 catalog 使用包内 YAML，并叠加用户/项目 YAML 与 `MUXDEV_CONFIG`。未知顶级 catalog section 会被报告。新任务路径不接受已废弃的 `profile`、`topology` 和 plugin 命名的 workflow 配置。

<!-- section:runtime -->
## 运行时选项

新任务使用 `intent`、`depth`、`workflow`、role 到 Provider override 和 gate。Depth 可取 `auto`、`simple`、`safe`、`deep`、`parallel`、`ci`。Gate 可取 `auto`、`safe`、`strict`、`ci`；strict/CI 可对 plan、write、shell、merge、external 操作要求审批。真实 Provider 工作必须设置有限的 `max_cost_usd`。

```toml
version = 2
gate = "safe"

[automation]
depth = "auto"
allow_parallel = true

[roles]
plan = "codex"
code = "codex"
test = "qwen"
review = "qwen"
```

<!-- section:workflow -->
## Workflow 与 Template

Workflow 是原生 DAG，包含 stage、role、依赖、条件、approval type、output schema、写权限和有界 loop 元数据。命令 template 使用 `workflow_templates` catalog key。旧 `workflow_plugins` key 与 `workflow plugin(s)` 别名已被有意删除。

只查看、不执行：

```powershell
muxdev graph export --workflow dev --json
muxdev workflow templates --json
muxdev workflow template spec-lite --json
```

<!-- section:provider -->
## Provider 与 Routing

Provider catalog 描述可执行命令、prompt transport、timeout、能力提示和 resume template。固定 `--provider` 会跳过质量选择，但不会跳过 policy 或 certification 检查。Role override 使用当前 role 名，例如 `--role code=codex --role review=qwen`。Mock/Replay 属于 simulation delivery mode，不能靠文字声明变成 production Evidence。

<!-- section:skills -->
## Skill 与 Context

Skill 包含 metadata、trust state、可选 role/stage binding，只有激活时才加载正文。项目 Skill 可以覆盖低优先级位置，但不会自动获得信任。使用 `muxdev skill catalog`、`muxdev skill explain <name>` 和 `muxdev skill lock`。Memory promotion 仍需显式操作，并保留 Evidence 引用。

<!-- section:security -->
## 安全敏感设置

Bearer token、签名私钥、Provider credential、transcript 和本地 Provider state 必须留在仓库外。API 应绑定 loopback。不要因为 Provider 自称安全就放宽 sandbox、network 或 reviewer 要求。Subject-bound waiver 应尽量窄，并在指纹或任务 subject 改变后失效。

<!-- section:examples -->
## 任务示例

```powershell
muxdev dev "fix parser edge case" --provider mock --safe
muxdev design "design a durable import pipeline" --deep --gate strict
muxdev refactor "split storage boundaries" --parallel --role review=qwen
muxdev dev "implement approved plan" --file .muxdev/task.toml --max-cost-usd 1.00
```

修改 gate 前阅读[安全与信任](security-and-trust.md)，daemon 路径和备份操作见[运维](operations.md)。
