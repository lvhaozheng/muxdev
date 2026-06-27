# muxdev Dashboard 深度优化设计方案

> 交付日期：2026-06-19  
> 目标：参考 botmux dashboard 的产品组织方式，结合 muxdev 当前功能定位、核心流程与现有代码实现，给出 dashboard 的功能排列、产品形态与分阶段落地方案。

## 0. 设计前提与范围

本次方案基于以下材料整理：

- muxdev 当前定位：本地优先的 Agentic SDLC Kernel / 多 AI Coding Agent 交付控制平面。
- 当前 dashboard 实现：Mission Control、Projects、Workflows、Tasks、Activity、Artifacts、Config、Global Config、Action Center、Provider Actions、muxdev Approvals、Validation。
- 当前核心流程：任务提交 -> 自动选择 workflow / topology / role -> provider 执行 -> provider action 或 muxdev approval -> evidence / report / diff -> rollback / ship / memory promotion。
- botmux GitHub 参照：本次检索命中多个同名项目，其中最贴近 muxdev 的是 [`deepcoldy/botmux`](https://github.com/deepcoldy/botmux)，定位为 Feishu/Lark 到 Claude Code、Codex、Gemini、OpenCode 等 AI coding CLI 的桥接层；另一个 [`skrashevich/botmux`](https://github.com/skrashevich/botmux) 更偏 Telegram bot 多机器人管理、反向代理和路由控制台，可作为“多实体运维 dashboard”次级参考。

### 0.1 GitHub 上 botmux 的可借鉴信号

`deepcoldy/botmux` 对 muxdev 的参考价值不在“页面长什么样”，而在它把 IM 会话、AI coding CLI、持续运行 session、人工接管和本地状态管理组织成一个可运维系统。结合仓库描述和近期 PR，建议 muxdev dashboard 吸收以下产品模式：

1. **会话是 dashboard 的一级对象**  
   `deepcoldy/botmux` 的核心描述是每个 DM、群组或话题会生成自己的实时 CLI session。映射到 muxdev，首屏不应只按项目或 workflow 展示，而要把“正在运行、等待处理、已卡住、可交付”的 run/session 直接推到首屏。
2. **群组 / 项目 -> 共享上下文 -> 当前状态快照**  
   [`PR #254`](https://github.com/deepcoldy/botmux/pull/254) 引入 whiteboard MVP：同一组内 agent 可共享 durable project context，并在 dashboard 里按 group -> whiteboard 层级查看，右侧显示详情。映射到 muxdev，应新增 Project Workspace 的“Shared State / Memory Board”区域，用于承载项目级当前事实、决策、风险和待推广记忆。
3. **人工接管、排队、恢复要显性化**  
   [`PR #256`](https://github.com/deepcoldy/botmux/pull/256) 涉及 queued busy message / idle probe / session readiness recovery；[`PR #196`](https://github.com/deepcoldy/botmux/pull/196) 涉及 adopted sessions、restart control 和 dashboard IPC probe。映射到 muxdev，Action Center 应把 provider busy、session lost、daemon restart、continue/retry/rollback 做成一组可操作的恢复队列。
4. **bot/provider defaults 与 usage ledger 要进入可视化治理**  
   近期 botmux 相关 PR 中出现 bot onboarding、Bot Defaults、sessionless bot card、workingDir/token usage 等 dashboard 信号。映射到 muxdev，System Center 不只是配置页，还要展示 provider 默认路由、工作目录、token/cost、健康探测和异常使用。
5. **右侧 detail panel 是高效产品形态**  
   botmux whiteboard dashboard 使用列表 + 右侧详情的组织方式，适合 muxdev 的 Action Center、Active Runs、Memory Board 和 Provider Actions：左侧负责筛选和排序，右侧负责解释、证据、命令复制和主操作。

因此，本方案会把 muxdev dashboard 从“工程表格集合”推进为：**Mission Control 首屏 + Project Workspace 共享上下文 + Run Detail 审计链路 + System Center 治理面**。

## 1. 一句话结论

muxdev dashboard 不应只是“任务列表 + 配置页”，而应升级为 **本地 AI 软件交付控制塔**：

> 第一眼回答“现在什么需要我处理”，第二眼回答“交付是否可信”，第三眼才进入项目、工作流、产物和配置细节。

当前 dashboard 已经有正确的数据基础，但信息排列仍偏工程后台：Projects 默认入口、Global Config 较重、任务详情表格堆叠较多。建议把默认产品形态调整为 **Mission Control 首页 + Project Workspace + Run Detail + System Center** 四层。

## 2. 产品定位重写

### 2.1 目标用户

| 用户 | 主要目标 | dashboard 要解决的问题 |
| --- | --- | --- |
| 个人开发者 | 用多个 AI coding provider 安全完成任务 | 少看日志，快速知道任务是否卡住、是否可合并 |
| 团队 Tech Lead | 查看多任务、多项目、多 provider 的交付状态 | 看风险、证据、阻塞和交付质量，而不是逐条读 run |
| 平台维护者 | 配置 provider、workflow、skills、policy、memory | 诊断系统健康，避免配置影响运行 |
| 审批/安全角色 | 决定是否放行 plan/write/shell/merge | 看清审批对象、证据、风险和回滚能力 |

### 2.2 核心价值

1. **把多 AI coding agent 变成可控的交付流水线**：dashboard 不展示“AI 很忙”，而展示“交付链路到哪一步”。
2. **把等待事项变成人能处理的下一步**：provider action、approval、recovery、plan feedback 必须统一进入行动中心。
3. **把可信交付显性化**：Evidence v2、test、review、diff、rollback、semantic merge、validator 都要服务于“是否可交付”。
4. **把配置从任务流里拆出去**：provider、workflow template、skills、memory、budget、safety 是系统能力，不应压住任务主线。

## 3. 当前 dashboard 问题诊断

| 现状 | 已做对的点 | 主要问题 | 优化方向 |
| --- | --- | --- | --- |
| Projects 默认入口 | 符合多 workspace / 多项目组织 | 用户第一眼看到项目，而不是待处理事项 | 首页改为 Mission Control，Projects 变成二级组织层 |
| Action Center 已存在 | 已聚合 provider action / approval / recovery | 权重还不够，像一个模块而不是入口 | 置顶成为首屏主对象，支持分组、严重度、批处理 |
| Workflows / Tasks / Activity / Artifacts / Config tabs | 避免所有信息堆在长页面 | 工作流看板按 role 分组，对新用户理解成本略高 | 增加 stage rail + task board 两种视图 |
| Provider Actions 与 Approvals 已分离 | 产品原则正确，安全边界清晰 | 文案和交互仍偏技术对象 | 转成“外部 provider 等你处理”和“muxdev 风险审批”两张不同卡 |
| Global Config 集中展示 | 对维护者有价值 | 首屏心智过重，容易干扰运行面 | 移到 System Center，默认只显示健康摘要 |
| Run Detail 表格非常全 | 审计能力强 | 人读成本高，缺少交付叙事 | 先给 Delivery Summary，再展开 evidence / traces / artifacts |

## 4. 目标信息架构

```text
muxdev Dashboard
├─ Mission Control：首页，默认入口
│  ├─ Command Bar：创建任务、选择项目、切换 provider/profile/gate
│  ├─ Action Center：待处理事项、审批、provider action、恢复、反馈
│  ├─ Active Runs：运行中任务、等待任务、失败任务、最近完成
│  ├─ Delivery Confidence：证据、测试、review、rollback、成本风险
│  └─ Health Strip：daemon、provider、budget、git safety、memory、skills
├─ Project Workspace：项目工作台
│  ├─ Overview：项目状态、默认 workflow、近期交付
│  ├─ Workflow Board：按 stage / role 查看运行链路
│  ├─ Task Board：按 todo / running / waiting / review / done / failed 查看
│  ├─ Activity：timeline、provider action、approval、events
│  ├─ Artifacts：report、diff、evidence、tests、transcripts、snapshots
│  └─ Config：项目级 profile、gate、roles、skills、memory
├─ Run Detail：单任务交付详情
│  ├─ Delivery Summary：任务目标、当前状态、下一步、可信度
│  ├─ Stage Timeline：每阶段输入、输出、耗时、provider attempt
│  ├─ Human Gates：审批对象、subject hash、风险、决策记录
│  ├─ Provider Handoff：attach、prompt、transcript、handled-and-continue
│  ├─ Evidence Center：Evidence v2、test、review、validator、semantic merge
│  └─ Recovery：continue、retry、rollback、report、diff
└─ System Center：系统配置与治理
   ├─ Providers：health、account、doctor、score、learning
   ├─ Workflows：templates、role topology、profiles
   ├─ Skills：catalog、lock、trust、activation
   ├─ Memory：inbox、accepted、quarantine、contradictions
   ├─ Policy：gates、budget、git safety、approval rules
   └─ Validation：single vs multi agent 实验、winner、成本、证据
```

## 5. 首屏功能排列

### 5.1 顶部 Command Bar

目的：让 dashboard 从“看板”变成“可操作入口”。

排列建议：

1. 左侧：muxdev 标识 + 当前 workspace / project switcher。
2. 中间：任务输入框，placeholder 为 `Describe what you want muxdev to do...`。
3. 右侧：provider、profile、gate、cost cap 的紧凑控件。
4. 最右：daemon 状态、刷新状态、设置入口。

关键约束：

- 创建任务默认走 `POST /api/tasks`，不让用户先理解 workflow。
- 高级项折叠在“Advanced run options”里，例如 role provider override、skills、CI block。
- 如果 provider 不可用，输入框仍可用，默认建议 mock demo。

### 5.2 Action Center 置顶

Action Center 是首屏主角，按“人要做什么”分组，而不是按数据来源分组。

优先级从高到低：

| 分组 | 来源 | 卡片主标题 | 主操作 |
| --- | --- | --- | --- |
| Provider 等你处理 | provider_actions | `Codex needs your action` / `Provider 需要你确认设计风格` | Copy attach command / Mark handled and continue |
| muxdev 风险审批 | approvals | `Approve shell gate` / `Approve reviewed design contract` | Approve / Deny |
| 失败恢复 | failed / blocked / errors | `Task needs recovery` | Continue / Report / Rollback |
| 设计反馈 | planning stage running | `Plan is open for feedback` | Submit feedback |
| 预算/安全暂停 | paused_budget / policy | `Budget or policy gate paused this run` | Adjust / Continue / Stop |

卡片字段建议：

- 项目、任务、run id、stage。
- 为什么需要处理。
- 不处理的影响。
- 主按钮 + 次按钮。
- 可复制命令。
- 相关证据入口：report / diff / transcript / policy subject。

### 5.3 Active Runs 区

展示 4 个队列：

1. Running：正在执行。
2. Waiting：等待人处理。
3. Failed / Recovery：失败但可恢复。
4. Recent Done：最近完成，可进入 review / ship。

每个 task card 默认只显示：任务名、状态、stage、provider、耗时、成本、风险。Hover / expand 再显示：stage elapsed、pending approvals、pending actions、error summary、report/diff/rollback。

### 5.4 Delivery Confidence 区

这是 muxdev 相比普通 agent board 的差异点。建议首页给每个活跃项目或最近完成任务一个交付可信度摘要。

字段建议：

| 字段 | 说明 |
| --- | --- |
| Evidence label | trusted / reviewable / risky / blocked |
| Confidence | 0-100 或 low / medium / high |
| Tests | passed / failed / missing |
| Review | blockers count |
| Rollback | available / missing |
| Diff | changed files / patch hash |
| Cost | tokens / USD / budget risk |

### 5.5 Health Strip

放在首屏下方或右侧，不展开技术细节，只给状态灯：

- Daemon：running / degraded。
- Providers：ready / partial / unavailable。
- Budget：within / near cap / exceeded。
- Git Safety：clean / dirty / no git / worktree issue。
- Skills：lock valid / drift。
- Memory：inbox count / contradictions。

点击后进入 System Center。

## 6. Project Workspace 设计

### 6.1 项目列表

当前 project sidebar 可以保留，但需要强化筛选与归档：

- 默认排序：有待处理事项 > 有运行任务 > 最近完成 > 其他。
- 项目卡展示：任务数、等待数、失败数、最近成本、默认 workflow。
- 支持隐藏项目，但文案应明确：隐藏不删除 workspace / run / evidence / files。

### 6.2 项目详情默认页：Overview，而不是直接 Workflow

项目详情建议新增 Overview tab，承载：

- 当前最需要处理的 1-3 件事。
- 近期 active runs。
- 默认 profile / gate / provider health。
- 最近交付产物。
- Memory inbox / skill lock 摘要。

然后再进入 Workflow / Tasks / Activity / Artifacts / Config。

### 6.3 Workflow Board

当前按 workflow -> role -> task card 分组是合理的，但建议补两层视角：

1. **Stage Rail**：横向显示 plan -> approve -> code -> test -> review -> fix。
2. **Role Lanes**：纵向显示 role / provider / attempts。

这样用户能同时理解“流程到哪一步”和“哪个 agent / provider 在负责”。

### 6.4 Task Board

与 Workflow Board 并列，用于非技术用户快速扫状态：

```text
Todo | Running | Waiting | Needs Review | Done | Failed
```

当前 `build_ux_overview` 已经产出 task_board，可直接复用，只是当前页面没有把它作为主要视图。

## 7. Run Detail 设计

### 7.1 顶部 Delivery Summary

单任务详情页第一屏建议不是表格，而是一个交付摘要：

- 任务目标：用户原始 task。
- 当前状态：running / waiting / completed / failed。
- 当前 stage：如 code、test、review。
- 人要做的下一步：来自 UX summary。
- 交付可信度：Evidence label + confidence。
- 关键风险：missing tests、review blocker、provider failure、budget。
- 快捷操作：Continue、Stop、Report、Diff、Rollback。

### 7.2 Stage Timeline

每个 stage 卡片包含：

- Stage id / role / provider。
- 输入：contract、context packet、memory refs。
- 输出：role result、artifact、summary。
- 耗时、attempt count、retry / fallback。
- 状态：completed / running / skipped / blocked。

### 7.3 Human Gates

审批卡要区分两类：

- muxdev approval：审批 muxdev 自己的 policy gate，如 plan/write/shell/merge。
- provider action：处理外部 provider CLI/session 的确认、登录、限流、问题补充。

这两类不要共用按钮文案，避免用户误以为 dashboard 会替他输入 provider yes/no。

### 7.4 Evidence Center

Evidence Center 的排列建议：

1. Final report。
2. Diff。
3. Tests。
4. Review blockers。
5. Evidence v2 manifest / evaluation。
6. Validator panel。
7. Semantic merge review。
8. Ledger / trace / transcripts。
9. Snapshots / rollback points。

默认展示摘要，展开后给完整表格。

## 8. System Center 设计

System Center 从当前 Global Config 演进而来，建议拆成 6 个模块。

| 模块 | 主要信息 | 操作 |
| --- | --- | --- |
| Providers | ready / partial / unavailable、score、account、doctor | install、account、doctor、refresh cache |
| Workflows | built-in workflows、workflow plugins、role topology | view template、render、set default |
| Skills | catalog、lock、trust、activation、events | activate、score、write lock |
| Memory | inbox、accepted、quarantine、contradictions | approve、quarantine、query |
| Policy | profile、gate、budget、git safety、approval rules | edit local config、dry-run check |
| Validation | validation experiments、winner、cost、evidence | open report、compare strategies |

## 9. 产品交互原则

1. **一个状态，一个主操作**：每张卡不要同时给 5 个同级按钮，主按钮永远只有一个。
2. **把技术对象翻译成人话**：`provider_actions` -> “Provider 等你处理”；`approvals` -> “muxdev 风险审批”。
3. **先结论，后证据**：先展示可信度标签和原因，再展开 evidence manifest。
4. **运行面和治理面分离**：Mission Control / Project Workspace 面向日常运行；System Center 面向配置治理。
5. **恢复能力必须显眼**：失败卡默认展示 Continue / Report / Rollback，不让用户去日志里找。
6. **不越权替用户操作 provider**：Dashboard 可以复制 attach、标记 handled、继续 run，但不替用户向外部 provider CLI 输入 yes/no。
7. **默认低噪音，高信息密度**：减少长表格首屏出现，改成可展开摘要。

## 10. 数据与 API 落地

### 10.1 可复用现有数据

| 能力 | 当前数据/接口 |
| --- | --- |
| 首页概览 | `GET /api/dashboard/overview` |
| 单任务详情 | `GET /api/tasks/{run_id}` |
| 创建任务 | `POST /api/tasks` |
| 继续任务 | `POST /api/tasks/{run_id}/continue` |
| provider action handled | `POST /api/tasks/{run_id}/actions/{action_id}/handled-and-continue` |
| approval approve / deny | `POST /api/approvals/{approval_id}/approve` / `deny` |
| report / diff | `GET /api/tasks/{run_id}/report` / `diff` |
| 事件流 | `WS /events` |
| product experience | `GET /api/product/experience` |
| provider health | `GET /api/providers/health` |
| setup status | `GET /api/setup/status` |

### 10.2 建议新增或增强字段

| 字段 | 层级 | 用途 |
| --- | --- | --- |
| `action.priority` | action_center | 支持排序和高亮 |
| `action.deadline_or_age` | action_center | 看出卡住多久 |
| `action.blocking_scope` | action_center | 标注阻塞 run / project / provider |
| `task.delivery_confidence` | task card | 首页展示可信度 |
| `task.evidence_summary` | task card / run detail | 汇总 tests / review / rollback / diff |
| `project.health` | project card | 汇总 waiting、failed、budget、provider issue |
| `provider_health.last_checked_at` | system center | 避免 dashboard 频繁实时 probe |
| `memory.inbox_count` | health strip | 提醒沉淀项目知识 |
| `skill_lock.drift_count` | health strip | 提醒 skill 变化风险 |

## 11. 分阶段路线图

### P0：信息架构重排，不动 runtime

目标：用现有 API 和数据完成产品形态升级。

- 首页改为 Mission Control：Command Bar、Action Center、Active Runs、Health Strip。
- Projects 降为项目工作台入口。
- Run Detail 首屏加入 Delivery Summary。
- 把 Global Config 改名为 System Center，并延迟加载。
- 把 task_board 数据真正渲染出来。
- Provider Action / Approval 文案分离。

验收标准：

- 用户打开首页 5 秒内能知道是否需要自己处理。
- waiting / failed / completed 三类任务有明确下一步。
- 不进入 Global Config 也能知道 provider / budget / git 大致状态。

### P1：可信交付可视化

目标：凸显 muxdev 差异点。

- 增加 Delivery Confidence 区。
- Run Detail 增加 Evidence Center 摘要。
- 任务卡展示 evidence label、tests、review blockers、rollback availability。
- Approval 卡展示 subject hash / subject summary / policy reason。
- Artifacts 支持按 report / diff / tests / transcript / snapshot 筛选。

验收标准：

- 完成任务后，用户不读 final_report 也能判断是否可 review / ship。
- 风险审批对象可追溯到 plan / patch / policy / evidence。

### P2：项目与团队工作流增强

目标：适配多项目、多任务、团队协作。

- 项目 Overview tab。
- 项目级筛选：provider、workflow、status、risk、cost。
- 批量处理低风险 action，例如批量 dismiss 已过期提示。
- Validation 与 provider learning 接入首页摘要。
- Memory inbox 和 contradictions 成为项目治理模块。

### P3：高级控制台能力

目标：把 dashboard 从观察面变成轻量控制面。

- Command palette。
- Workflow template preview / render。
- Provider score 趋势图。
- 多 repo orchestration 可视化。
- Parallel conflicts / semantic merge 的图形化审查。
- 可分享的 run review 页面，隐藏敏感 transcript。

## 12. 推荐首版页面草图

```text
┌─────────────────────────────────────────────────────────────┐
│ muxdev Mission Control   [Project ▼]  [New task........]     │
│ Provider:auto  Profile:squad  Gate:safe  Budget:$0.50  ● OK  │
├─────────────────────────────────────────────────────────────┤
│ Action Center                                                │
│ ┌ Provider needs action ────────────────┐ ┌ Approval ──────┐ │
│ │ codex / run_123 / code                │ │ shell gate     │ │
│ │ Apply this change? Handle in CLI.     │ │ pytest command │ │
│ │ [Copy attach] [Handled & Continue]    │ │ [Approve][Deny]│ │
│ └───────────────────────────────────────┘ └────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│ Active Runs                                                  │
│ Running        Waiting        Failed         Recent Done      │
│ run_a code     run_b action   run_c recover  run_d reviewable│
├─────────────────────────────────────────────────────────────┤
│ Delivery Confidence           Health                         │
│ reviewable 83%                Providers: 2 ready / 1 partial │
│ tests passed, rollback ok     Budget ok, Git dirty, Skills ok │
└─────────────────────────────────────────────────────────────┘
```

## 13. 关键取舍

1. 不建议把 dashboard 做成纯 Kanban。muxdev 的核心不是任务协作工具，而是可信交付控制平面，Kanban 只能作为一个视图。
2. 不建议把 provider / skills / workflow config 放在首屏主列。它们很重要，但属于系统健康和治理，不是用户每天最先要处理的问题。
3. 不建议把 Evidence 做成只有下载链接的产物列表。Evidence 是 muxdev 的产品壁垒，应转成“交付可信度”的用户语言。
4. 不建议隐藏恢复操作。AI coding 真实环境里失败、限流、认证、provider prompt 都是常态，恢复流程越显眼，用户越信任系统。

## 14. 待确认问题

1. 是否能补充 botmux 实际 dashboard 截图或线上访问权限？如果可以，应二次校准“实体层级、首屏排序、视觉密度、操作模型”。
2. muxdev dashboard 的主要使用场景更偏个人本地，还是团队共享？这会影响是否需要权限、审计分享页、多人审批。
3. 当前版本是否计划引入前端框架？若仍保持单文件 HTML，应优先做 P0/P1 的信息架构与数据摘要，不做复杂图表。
4. 是否希望 dashboard 具备真正的“创建任务”主入口？当前 CLI/TUI 是强入口，dashboard 若加入 Command Bar，会改变产品心智。

## 15. 最终建议

短期最优解是：**不推翻当前 dashboard，实现“首屏重排 + 交付摘要 + System Center 改名分层”**。

这样风险最低，因为 muxdev 已经具备完整数据基础；真正缺的是产品排序。先让用户打开 dashboard 时立刻看到：

- 现在有没有事需要我处理。
- 哪些任务正在跑、哪些卡住、哪些完成。
- 完成的东西是否可信。
- 如果失败，我怎么恢复。
- 如果要配置系统，我去哪里看。

这会让 muxdev 从“多 agent 运行记录页”变成“本地 AI 交付控制塔”。
## 2026-06 Dashboard Implementation Update

- All dashboard lists default to three visible items with an expand/collapse
  control: Needs My Action, running tasks, project buckets, provider readiness,
  workflow templates, and role routing.
- Dashboard separates model roles from gates. `human_gate` and `delivery_gate`
  are rendered as workflow stage types, not roles.
- Config now presents workflow template readability:
  best-for scenarios, stage flow, model roles, human review points, internal
  delivery gates, and supported providers.
- Role/provider display is route-oriented: configured provider, fallback
  provider, effective provider, readiness, and setup/doctor hints.
