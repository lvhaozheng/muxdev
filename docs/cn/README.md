# muxdev 文档

<!-- section:overview -->
## 概览

muxdev 是包裹在编码智能体 CLI 外的一套本地优先控制面。它把用户请求转换为持久化任务，选择原生 Workflow DAG，在隔离工作区中调用类型化 Provider 适配器，执行审批与验证门禁，并记录之后可检查、可回放的证据。

本文档只描述当前真实代码。已经过期的路线图、课程稿、阶段周报、面试材料和飞书生成物已被有意删除。

<!-- section:audience -->
## 适合谁阅读

- 希望运行安全离线演示或受控 Codex/Qwen 工作流的使用者。
- 负责守护进程、恢复、备份和可信交付的运维人员。
- 扩展 Provider、Workflow、Skill、存储或验证逻辑的贡献者。
- 想理解“持久化智能体运行时”和“聊天循环”差异的应届毕业生。

<!-- section:map -->
## 文档地图

- [快速开始](getting-started.md)：安装并运行第一个可信任务。
- [架构](architecture.md)：边界、执行链与存储设计。
- [概念速查](concepts.md)：核心机制速查表与深入解释。
- [配置](configuration.md)：优先级、Provider、Workflow、门禁和 Skill。
- [运维](operations.md)：守护进程、恢复、备份与诊断。
- [安全与信任](security-and-trust.md)：威胁模型、审批、证据和签名。
- [开发](development.md)：仓库结构、测试、扩展点和兼容规则。

英文文档：[English](../en/README.md)。

<!-- section:quickstart -->
## 快速开始

```powershell
python -m pip install -e ".[test]"
muxdev setup --project
muxdev demo --scenario trusted-delivery-v1 --mode replay
muxdev dev "add a small validated change" --provider mock --json
muxdev status latest
muxdev evidence latest
```

内置的 `mock` 和 `replay` Provider 不需要外部账号。连接真实 Provider CLI 前，请先阅读[快速开始](getting-started.md)。

<!-- section:status -->
## 产品状态

当前仓库仍是候选发布版本，不能据此声称某个真实 Provider 普遍更好。Replay Benchmark 是确定性的工程检查；真实质量结论必须来自单独授权且有预算上限的现场 Benchmark。v7 SQLite 与已有运行 artifact 继续可读，但已废弃的 v0.2 命令别名不再支持。
