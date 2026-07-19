# 开发

[English](../en/development.md)

<!-- section:setup -->
## 本地环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
ruff check .
python scripts/verify_docs.py
pytest -q -m "not release"
```

除非明确在范围内，否则保留已有未提交工作。使用小 patch，保持 v7 数据兼容，并先测试最窄的受影响边界。

<!-- section:layout -->
## 源码布局

- `domain`：稳定类型契约与状态规则。
- `application`：生命周期、命令、查询和协调用例。
- `runtime`：原生调度、Stage 执行、恢复、worktree 与合并。
- `providers`：类型化 adapter、parser、certification 与 Provider policy。
- `storage`：SQLite、migration、event、repository、read model 和文件。
- `daemon`：持久化 worker、任务后端、进程生命周期与 event bus。
- `services`：Evidence、routing、RAG、attestation 等有边界的产品能力。
- `cli`、`api`、`presentation`：入口与渲染层。

<!-- section:tests -->
## 测试策略

纯契约使用单元测试，SQLite/runtime/API 使用集成测试，release 测试只用于显式打包或 live gate。本地必跑 Ruff、双语文档验证、针对性测试、非 release 全量和跨平台 quick suite。Runtime 测试必须覆盖顺序/并行等价性、无效结构化结果、只读越界、重试、Provider Action 暂停恢复、lease fencing、冲突和最终交付。

<!-- section:architecture-rules -->
## 架构规则

依赖始终向内。Domain 不导入外层。API/CLI handler 只做校验/翻译并调用 application service，不能编排 Blackboard 或 Runtime。Provider 不导入 daemon 或 UI。Presentation 只读。所有 Provider 执行必须使用 `StageExecutionInput` 与 `StageExecutionResult`；不得重新加入 `run_stage(**kwargs)` 兼容或运行时签名探测。

<!-- section:provider-extension -->
## 新增 Provider

实现类型化 adapter、Provider 专用 event parser、静态 probe、certification Evidence、cancel 行为和可选原生 resume template。Subprocess environment 必须 allowlist，transcript 必须位于受控 session 目录。补充契约、错误分类、stale fingerprint、certification 和离线 fixture 测试。绝不能只根据品牌名推断能力。

<!-- section:workflow-extension -->
## 新增 Workflow 或 Skill

Workflow Stage 需要稳定 ID、role、依赖、写/只读 policy、schema、delivery target 和有上限的 loop 元数据。必须验证导出的原生 DAG 和必需 deliverable。Skill 需要合法 metadata、显式 trust、受限 role/stage binding、lock 验证，并用测试证明不可信内容不会自动选择。

<!-- section:migration-extension -->
## 修改存储

添加有序、带 checksum 的 migration；不能修改已应用 migration。Event 与 projection 必须原子、幂等、可 replay、受 fencing 保护。增加冻结的改造前 fixture，以及 query、Evidence、resume、backup/restore 和并发所有权测试。升级时不得重置用户数据库。

<!-- section:docs-extension -->
## 修改文档

`docs/en` 与 `docs/cn` 必须恰好包含相同的八个文件，并具有顺序相同的 `<!-- section:id -->` 标记。信息应等价，但不要求逐句翻译。所有相对链接、源码路径和 counterpart 链接必须存在。不要在 `docs` 根目录新增散落文件。

<!-- section:build-install -->
## 构建与安装冒烟测试

```powershell
python -m build
python -m venv .wheel-smoke
.\.wheel-smoke\Scripts\python -m pip install dist\*.whl
.\.wheel-smoke\Scripts\muxdev --version
.\.wheel-smoke\Scripts\muxdev graph export --json
```

构建目录是可丢弃的 release artifact；源码、文档、数据库和用户 run 不是。打 tag 前必须在干净环境验证 wheel。
