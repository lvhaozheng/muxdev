# muxdev

muxdev 是一个本地优先的 AI Agent 可信交付控制面。它只解决一件事：让一次 Agent 变更能够恢复、验证、解释和审计。

系统固定为四类工作流 `change / design / review / test`、三档 Profile `lite / standard / strict`、五个内置 Skill，以及唯一的 EvidencePolicy 和 Gate Engine。Provider 输出被视为声明；Diff、Runtime 重放的检查结果、目标摘要、Reviewer 身份、人工交互和事件链才是可采信事实。

## 快速开始

需要 Python 3.11+。

```powershell
python -m pip install -e ".[test]"
muxdev init
muxdev run "add a deterministic marker" --workflow change --profile lite --provider mock
muxdev list
muxdev evidence verify <run-id>
```

`mock` 只验证控制面、恢复和门禁语义，不代表真实 Provider 质量。`standard` 和 `strict` 要求 Runtime 可确认的独立 Reviewer；`strict` 还要求人工确认，并执行条件安全评审。

## 核心边界

- `TaskService`：创建、查询、恢复、取消任务和处理交互。
- `RunEngine`：执行固定阶段、持久化事件、调用 Provider、重放确定性检查、运行门禁和恢复任务。
- `ProviderAdapter.execute(StageExecutionInput)`：唯一 Provider 执行接口，不接受 Provider 自报 Evidence 或门禁结论。
- `EvidencePolicy`：唯一硬门禁规则源；项目可通过 `evidence-policy.yaml` 覆盖 Requirement 和 Profile 映射。
- `evidence-report.json`：每次运行唯一 Evidence 事实报告；显式导出时才生成 `attestation.dsse.json`。

Gate 结果固定为 `PASS / BLOCKED / WAITING_HUMAN`。Scorecard 只计算 completeness、reproducibility、integrity、independence 四个可解释维度，不能覆盖硬门禁。

## 固定产品表面

- 29 个 CLI 叶子命令：运行、交互、Evidence、路由、Provider、Skill、配置、迁移和服务。
- 18 个 HTTP 接口：围绕 runs、interactions、events、artifacts、Evidence、Providers、Skills 和 routing。
- 8 个 MCP 工具：run/get/list/resume/cancel/respond/get_evidence/verify_evidence。
- 12 张 SQLite 核心表，v7 数据首次启动时执行备份、临时导入、校验和原子切换。

完整命令可运行 `muxdev --help`。设计与实测结果见：

- [精简报告](release-artifacts/simplification-report.md)
- [架构图](release-artifacts/architecture-diagrams.md)
- [开源架构调研与取舍](release-artifacts/open-source-research.md)
- [Evidence v3 对照样例](release-artifacts/evidence-v3-example.json)
- [AI Agent Infrastructure Case Study](release-artifacts/ai-agent-infrastructure-case-study.md)
- [面试讲述](release-artifacts/interview-narratives.md)
- [中英文简历 Bullet](release-artifacts/resume-bullets.md)
- [分层测试与耗时](release-artifacts/verification-report.md)
- [English reference](docs/en/README.md)

## 验证

```powershell
python -m ruff check src/muxdev scripts tests
python -m pytest -q
python scripts/architecture_metrics.py
```

本项目借鉴 OpenHands typed append-only events、Codex submission/event/approval 分离、DBOS/LangGraph 的恢复语义、SWE-agent 的 Agent/环境边界、Aider repo map 的按需确定性上下文，以及 in-toto 的 Subject/Predicate/Envelope 分层；不引入通用 DAG 或新的工作流运行时依赖。
