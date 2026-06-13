# Product Experience Readiness

本指南用于验收 muxdev 的产品化入口：一行安装、项目初始化、provider wizard、`MUXDEV.md`、预算与 Git 安全、规则与技能可见性，以及 Mission Control。

## Delivered Surface

- One-line install path: `pipx install muxdev`，也支持 `uv tool install muxdev` 和 editable repo install。
- Short quickstart: `muxdev setup --project`、`muxdev provider setup`、`muxdev demo --mock`、`muxdev`。
- Provider setup wizard: `muxdev provider setup` 与 `/api/product/experience` 暴露 install、account、doctor steps。
- Project context anchor: project setup 生成 `MUXDEV.md`，也可用 `muxdev context --write` 创建。
- Mission Control: Dashboard 展示 Product Experience、budget、provider health、Git safety、rules、skills、Kanban task management 和 web/IDE extension API hints。
- Git-native safety: 产品面指向 diff、dry-run ship、rollback、Git restore/revert、reviewed Git commit，不直接执行破坏性动作。
- Rules and skills visibility: active profile、gate、role bindings、discovered skills 和 management commands 集中可见。
- Cost visibility: active tasks、total cost、token usage、high-cost task count 和 budget controls 可在 API/Dashboard/CLI 中查看。

## 快速验收

```powershell
muxdev setup --project
muxdev provider setup
muxdev context --write
muxdev experience
muxdev dashboard
```

通过标准：

- `MUXDEV.md` 存在，且不会在未要求 overwrite 时覆盖用户内容。
- `muxdev experience --json` 返回 quickstart、provider setup、budget、Git safety、project context、rules/skills。
- Dashboard 首页出现 Mission Control 产品区域。
- Provider actions 与 muxdev approvals 在 UI 中明确分开。

## API

```powershell
curl http://127.0.0.1:8788/api/product/experience
curl http://127.0.0.1:8788/api/dashboard/overview
curl http://127.0.0.1:8788/api/setup/status
curl http://127.0.0.1:8788/api/providers/health
curl http://127.0.0.1:8788/api/ux/overview
```

## Dashboard Information Architecture

- `Projects` is the default tab. A task belongs to the project represented by the task execution `workspace`.
- Each project starts with `Workflows`, grouped as workflow -> role -> task cards. Task cards expose deeper status on hover/focus without expanding the whole page.
- Each project uses `Workflows`, `Tasks`, `Activity`, `Artifacts`, and `Config` tabs so timeline, provider actions, approvals, events, evidence, reports, tests, transcripts, rollback, and semantic merge output do not stack into one long page.
- Project cards support `Hide`, which removes the project from the Dashboard without deleting the workspace, runs, evidence, or files. `include_hidden=true` plus the restore API can bring it back.
- `Global Config` centralizes role templates, providers, budget, safety gates, Skills Catalog, and Workflow Templates.

## 回归

```powershell
python -B -m pytest -q -k "product_experience or provider_setup or project_setup"
python -B -m pytest -q
```
