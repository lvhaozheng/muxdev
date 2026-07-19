# 架构

[English](../en/architecture.md)

<!-- section:boundaries -->
## 分层边界

依赖方向始终向内：`cli`、`api`、presentation 是入口层；application service 暴露用例；daemon coordination 管理队列；runtime 执行 stage；domain 保存稳定契约；storage 和 Provider adapter 实现端口。Domain 不得导入 runtime、storage、daemon、API、CLI 或 UI。API handler 只调用任务命令/查询服务，不能实例化 `SupervisorRuntime` 或直接打开 SQLite。

<!-- section:request-flow -->
## 请求链路

```text
CLI / HTTP / TUI
  -> TaskCommandService
  -> 持久化 RunSpec + execution job
  -> 有界 worker pool + 带 fencing 的 lease
  -> SupervisorRuntime 门面
  -> 原生 Workflow DAG 调度器
  -> StageExecutionInput -> Provider adapter -> StageExecutionResult
  -> 验证、Evidence、projection、报告、attestation
```

查询走独立的 `TaskQueryService` 和 read model。这样状态展示就不会意外启动任务或修改状态。

<!-- section:runtime -->
## 运行时与 Stage 执行

`SupervisorRuntime` 是外部门面。调度器根据依赖和条件计算可运行 stage。每个 Provider 只实现一个类型化方法：`execute(StageExecutionInput) -> StageExecutionResult`。输入绑定 run、stage、role、Provider、worktree、policy、context、Skill、session 目录和 attempt；结果包含内容、artifact 身份、usage、event、退出状态和检测到的动作。Test/Review schema 采用 fail-closed 验证；进程退出码为 0 绝不等于任务成功。

<!-- section:parallel -->
## 并行隔离与合并

可并行的写 stage 使用彼此独立的 worker workspace。系统对 workspace 计算哈希，捕获支持二进制的 patch，并把 patch 绑定到基线哈希。确定性合并会拒绝基线漂移、重叠写入、冲突标记和语义审查 blocker。只读 stage 还会检查是否发生写入。两个写 stage 不会共享同一个可变 checkout。

<!-- section:storage -->
## 持久化状态

Blackboard 使用 SQLite v7 schema、WAL/FULL 持久化、带校验和的 migration、事务、事件优先的生命周期修改和查询 projection。受控目录保存 task context、workflow 快照、Provider transcript、artifact、diff、report、Evidence、ledger 和 attestation。持久化 job 通过 lease、heartbeat、cancellation token、fencing number 和 reconciliation 保证旧 worker 丢失所有权后不能提交。恢复旧数据时，历史 `langgraph` 元数据映射到原生调度器，不重置数据库布局。

<!-- section:extension -->
## 扩展点

- Provider adapter：类型化执行，以及可选的 probe、certification、event、cancel、resume。
- Workflow：包含 stage、依赖、条件、gate、schema 和 loop 元数据的 YAML DAG。
- Skill：受治理的 prompt/context 包，可显式选择或通过可信绑定启用。
- Context：有预算约束的 task、RAG、Memory、blocker 与 Provider response 信息源。
- Validation：结构化输出契约、交付门禁、Benchmark Replay 和独立审查。

规则见[开发文档](development.md)，更深入的心智模型见[概念速查](concepts.md)。
