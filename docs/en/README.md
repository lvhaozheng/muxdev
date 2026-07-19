# muxdev Documentation

<!-- section:overview -->
## Overview

muxdev is a local-first control plane around coding-agent CLIs. It turns a user request into a durable task, selects a native workflow DAG, runs typed Provider adapters in isolated workspaces, applies approval and validation gates, and records evidence that can be inspected or replayed later.

The documentation describes the code that exists today. Archived roadmaps, course notes, weekly reports, interview material, and generated Feishu assets were intentionally removed.

<!-- section:audience -->
## Who This Is For

- Users who want a safe offline demo or a controlled Codex/Qwen workflow.
- Operators responsible for the daemon, recovery, backups, and trusted delivery.
- Contributors extending Providers, workflows, Skills, storage, or validation.
- New graduates learning how a durable agent runtime differs from a chat loop.

<!-- section:map -->
## Documentation Map

- [Getting started](getting-started.md): installation and the first trusted task.
- [Architecture](architecture.md): boundaries, execution flow, and storage design.
- [Concepts](concepts.md): quick reference plus deep explanations of core mechanisms.
- [Configuration](configuration.md): precedence, Providers, workflows, gates, and Skills.
- [Operations](operations.md): daemon operation, recovery, backups, and diagnostics.
- [Security and trust](security-and-trust.md): threat model, approvals, evidence, and signatures.
- [Development](development.md): repository layout, tests, extensions, and compatibility rules.

Chinese documentation: [中文入口](../cn/README.md).

<!-- section:quickstart -->
## Quick Start

```powershell
python -m pip install -e ".[test]"
muxdev setup --project
muxdev demo --scenario trusted-delivery-v1 --mode replay
muxdev dev "add a small validated change" --provider mock --json
muxdev status latest
muxdev evidence latest
```

The built-in `mock` and `replay` Providers require no external account. Use [Getting started](getting-started.md) before connecting a real Provider CLI.

<!-- section:status -->
## Product Status

The repository is a release-candidate line, not proof that any real Provider is universally better. Replay benchmarks are deterministic engineering checks; live quality claims require a separately authorized, budgeted benchmark. The v7 SQLite schema and existing run artifacts remain readable while retired v0.2 command aliases are intentionally unsupported.
