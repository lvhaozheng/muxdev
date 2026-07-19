# 快速开始

[English](../en/getting-started.md)

<!-- section:requirements -->
## 环境要求

- Python 3.11 或更新版本。
- Git，用于隔离 worktree 和基于 patch 的并行合并。
- 支持 Windows、Linux、macOS；下面示例使用 PowerShell 语法。
- Provider 账号不是必需项，因为 `mock` 与 `replay` 可以离线工作。

<!-- section:install -->
## 从当前仓库安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
muxdev --version
```

安装构建产物时，可使用 `pipx install <wheel>` 或 `uv tool install <wheel>`。发布前必须执行[开发文档](development.md#构建与安装冒烟测试)中的 wheel 冒烟测试。

<!-- section:first-run -->
## 运行离线可信路径

```powershell
muxdev setup --project
muxdev doctor
muxdev demo --scenario trusted-delivery-v1 --mode replay
muxdev dev "document the current health endpoint" --provider mock --json
```

`setup` 在 `.muxdev` 下写入项目级默认配置；`doctor` 检查 Git、端口、存储、worktree 创建和确定性的 Mock Provider。任务会提交给本地 daemon，可用 `muxdev status latest`、`muxdev report latest` 和 `muxdev evidence latest` 查看。

<!-- section:real-provider -->
## 连接真实 Provider

```powershell
muxdev provider detect --json
muxdev provider setup
muxdev provider certify codex
muxdev dev "add a focused unit test" --provider codex --gate safe
```

“检测到”不等于“已认证”。检测只说明 CLI 看起来支持哪些能力；认证会把版本/指纹与有证据支撑的能力绑定。高风险工作可能要求更强隔离、不同的只读 reviewer，或绑定具体 subject 的人工 waiver。不要把凭据写进任务文本。

<!-- section:lifecycle -->
## 跟踪任务生命周期

```powershell
muxdev tasks
muxdev status latest
muxdev approvals
muxdev actions
muxdev continue latest
muxdev diff latest
muxdev report latest
```

muxdev Approval 是 muxdev 自己管理的策略决定；Provider Action 表示外部 CLI 正等待登录、确认、限流恢复或其他人工输入。应先处理 Provider 会话，记录响应，再继续任务。

<!-- section:troubleshooting-next -->
## 失败时怎么办

先运行 `muxdev doctor`，查看 `muxdev status latest` 和最新报告。任务被 blocked 是“失败时关闭”的安全设计。不要通过删除 `.muxdev` 或重置 SQLite 来“修复”；请使用[运维文档](operations.md)中的恢复和备份流程。想理解 lease、证据和回滚背后的模型，请阅读[概念速查](concepts.md)。
