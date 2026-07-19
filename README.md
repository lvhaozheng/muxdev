# muxdev

muxdev is a local-first trusted delivery control plane for coding-agent CLIs. It provides durable tasks, native Workflow DAGs, typed Provider adapters, approvals, isolated parallel workspaces, validation gates, Evidence, recovery, and signed attestations.

muxdev 是面向编码智能体 CLI 的本地优先可信交付控制面，提供持久化任务、原生 Workflow DAG、类型化 Provider adapter、审批、并行隔离、验证门禁、Evidence、恢复和签名 attestation。

## Quick Start / 快速开始

```powershell
python -m pip install -e ".[test]"
muxdev setup --project
muxdev demo --scenario trusted-delivery-v1 --mode replay
muxdev dev "add a small validated change" --provider mock
muxdev status latest
```

No external Provider account is required for Mock or Replay. Mock/Replay output is simulation evidence, not a real Provider quality claim.

Mock 与 Replay 不需要外部 Provider 账号；它们的输出属于 simulation Evidence，不能视为真实 Provider 质量结论。

## Documentation / 文档

- [English documentation](docs/en/README.md)
- [中文文档](docs/cn/README.md)
- [English concept reference](docs/en/concepts.md)
- [中文概念速查](docs/cn/concepts.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

Python 3.11+ is required. The v7 SQLite and existing run-artifact layout remain readable; retired v0.2 Flow, plugin/profile/topology, media, TTS, and LangGraph execution surfaces are intentionally unsupported.
