# Getting Started

[中文](../cn/getting-started.md)

<!-- section:requirements -->
## Requirements

- Python 3.11 or newer.
- Git for isolated worktrees and patch-based parallel merge.
- Windows, Linux, or macOS; commands below use PowerShell syntax.
- A Provider account is optional because `mock` and `replay` work offline.

<!-- section:install -->
## Install From This Repository

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
muxdev --version
```

For an installed artifact, use `pipx install <wheel>` or `uv tool install <wheel>`. Run the wheel smoke test described in [Development](development.md#build-and-install-smoke-test) before publishing.

<!-- section:first-run -->
## Run the Offline Trusted Path

```powershell
muxdev setup --project
muxdev doctor
muxdev demo --scenario trusted-delivery-v1 --mode replay
muxdev dev "document the current health endpoint" --provider mock --json
```

`setup` writes project-scoped defaults under `.muxdev`. `doctor` checks Git, ports, storage, worktree creation, and the deterministic Mock Provider. Task submission goes to the local daemon; use `muxdev status latest`, `muxdev report latest`, and `muxdev evidence latest` to inspect it.

<!-- section:real-provider -->
## Connect a Real Provider

```powershell
muxdev provider detect --json
muxdev provider setup
muxdev provider certify codex
muxdev dev "add a focused unit test" --provider codex --gate safe
```

Detection is not certification. Detection reports what a CLI appears to support; certification binds a version/fingerprint to evidence-backed capabilities. High-risk work can require stronger isolation, a distinct read-only reviewer, or a subject-bound waiver. Never paste credentials into task text.

<!-- section:lifecycle -->
## Follow a Task Lifecycle

```powershell
muxdev tasks
muxdev status latest
muxdev approvals
muxdev actions
muxdev continue latest
muxdev diff latest
muxdev report latest
```

A muxdev Approval is a policy decision owned by muxdev. A Provider Action means the external CLI needs login, confirmation, rate-limit recovery, or another human response. Handle the Provider session first, record the response, then continue the task.

<!-- section:troubleshooting-next -->
## If Something Fails

Run `muxdev doctor`, inspect `muxdev status latest`, and read the latest task report. A blocked task is intentionally fail-closed. Do not delete `.muxdev` or reset SQLite to “fix” it; use the recovery and backup procedures in [Operations](operations.md). For mental models behind leases, evidence, and rollback, read [Concepts](concepts.md).
