# Skills 与可信交付设计

## 1. 目标与边界

MuxDev 把 Skill 视为“受版本约束的指令资料”，而不是新的权限主体。Skill 可以说明如何工作，但不能扩大 Assignment 已授予的网络、密钥、写入、Shell 或 MCP 权限；`scripts/` 永不因绑定或加载而自动执行。

本设计借鉴 Charter 的三个产品闭环：

- [Managed Skills](https://github.com/longyunfeigu/Charter/blob/f9bbca56d914d3549b80345cc4cd2ff54aff516c/docs/adr/ADR-0015-managed-skills.md)：集中目录、渐进披露和托管副本；
- [External Skill Sources](https://github.com/longyunfeigu/Charter/blob/f9bbca56d914d3549b80345cc4cd2ff54aff516c/docs/adr/ADR-0019-external-skill-sources.md)：连接、复制、信任审核和来源生命周期；
- [Skills Usage Insight](https://github.com/longyunfeigu/Charter/blob/f9bbca56d914d3549b80345cc4cd2ff54aff516c/docs/adr/ADR-0037-skills-usage-insight.md)：使用事件、统计和回放。

Charter 的 live revision 适合个人知识管理，但不能保证一次交付在源目录变化后仍可复现；其 replay receipt 也明确是 unsigned。MuxDev 因此保留内容寻址快照、事件哈希链、Evidence v3 以及 DSSE/in-toto 签名链，只复用其信息架构和渐进式体验。

## 2. 来源、信任与发现

统一目录发现以下来源：

- MuxDev 内置 Skills；
- 项目中的 `.muxdev/skills`、`.agents/skills`、`.codex/skills`、`.claude/skills`、`.deepcode/skills` 及兼容目录；
- 用户显式连接的只读目录；
- 导入到用户数据目录的托管副本。

外部来源初始为 `needs_review` 且禁用。审核后可设为 `user_trusted` 或 `org_trusted` 并启用。断开只修改目录记录，不删除原目录或托管副本。复制导入不会写回原来源。

扫描前执行真实路径 containment 和符号链接逃逸校验；每个来源最多 500 个文件、20 MiB，每次加载单个文件最多 256 KiB。同名 Skill 使用 `name@source` 消歧。Codex、Claude Code 和 Deep Code 原生目录会显示原生兼容性；Deep Code 同时原生识别 `.agents/skills`。原生调用是否可验证仍取决于运行时可观测事实。

## 3. 绑定与冻结

项目、Conversation 和 Assignment 都可以绑定 Skill，优先级为 Assignment、Conversation、Project。绑定保存：

- qualified name、版本、来源和 tree SHA-256；
- 文件清单及单文件哈希；
- Skill 声明权限和消费者兼容性；
- `required` 与启用状态。

会话启动时将绑定版本复制到 `.muxdev/skill-snapshots/<tree-hash>/`，并保存只读、内容寻址的 `SkillSnapshot`。后续源漂移只在重新扫描后显示给用户，不会改变当前 generation 的加载内容。升级必须显式生成新绑定和新快照。

## 4. 渐进加载和审计

Bootstrap 只注入名称、描述、版本和加载命令，不注入所有正文：

```text
muxdev skill load <qualified-name> [--file SKILL.md]
```

CLI 从 `MUXDEV_CONTROL_TOKEN` 取得短期会话身份。加载器校验 token、Conversation、Assignment、当前冻结版本、文件 containment 和大小，然后返回正文并追加 `skill.loaded`。产品输入框的 `/skill:<name>` 使用同一加载路径，经 Runtime 通道注入当前 Session。

每条 `SkillUsageV1` 记录 session、assignment、generation、版本、来源、文件、哈希、消费者、激活方式和捕获等级：

- `verified`：MuxDev 加载器校验并读取冻结文件；
- `observed`：外部 CLI 提供可识别事件，但 MuxDev 未控制读取；
- `claimed`：只存在 Agent 自述；
- `unavailable`：无法观察或必需加载缺失。

Codex、Claude Code、Deep Code 的原生目录发现只表示兼容性。未经统一加载器确认的原生调用不得升级为 `verified`。

## 5. Evidence 和回放

Evidence v3 报告增加 Skills 清单。`required` Skill 会派生一个 Runtime Evidence requirement；只有绑定 revision 相同、binding 相同且属于当前 Session generation 的 `verified` 使用事件才能满足。Generation 重启后必须重新加载，否则交付 Gate 为 `BLOCKED`。

回放采用 Recap、Explore、Verify 三层：

- Recap：使用了哪些 Skill、版本和来源；
- Explore：加载了哪个文件、由谁触发、用于哪个 Assignment；
- Verify：文件哈希、快照树哈希、事件链和签名验证。

界面和导出必须区分已记录事实、Agent 自述和不可观测推理。Skills 清单参与 Evidence 报告，但不会把指令内容本身误当成完成证明。

## 6. 数据和接口

项目 schema v14 新增 `skill_bindings`、`skill_snapshots` 和 `skill_usage`；Workbench schema v2 新增全局 `skill_sources`。历史 Run、Skill Lock、Conversation 快照和 Evidence 不重写。

产品接口覆盖来源连接/复制、审核、启停、重扫、断开，项目目录/绑定，Conversation 显式激活以及使用查询。Skills 页面展示来源漂移、文件审计、脚本数量、Agent 兼容性、项目绑定、必需门禁和使用次数。
