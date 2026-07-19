# 安全与信任

[English](../en/security-and-trust.md)

<!-- section:boundary -->
## 信任边界

muxdev 是本地优先系统，假设一名 OS 用户控制机器。Loopback 认证可减少意外浏览器/API 访问，但无法防御已经以同一用户运行的恶意进程。Provider CLI、模型输出、workspace 文件、Skill、检索文本和恢复 archive 都是不可信输入。需要更强边界时，应使用独立 OS 账号或 sandbox。

<!-- section:threats -->
## 主要威胁

- Prompt injection 导致未授权工具调用或写入。
- Provider 在没有有效契约时声称 test/review 成功。
- 并发 worker 互相覆盖，或旧 worker 提交结果。
- Plan、command、fingerprint 或 artifact subject 改变后复用旧审批。
- Secret 泄漏到 prompt、transcript、artifact、log 或 bundle。
- SQLite migration、backup archive、Evidence 或 attestation 被篡改。
- 把本地自声明签名身份误当成组织身份。

<!-- section:approvals -->
## 审批与人工控制

Policy approval 绑定 canonical subject hash，使用前重新检查。Plan、write、shell、merge、external access、isolation downgrade 和 reviewer waiver 可以分别要求决策。Provider Action 不是 muxdev approval，也不会自动确认。拒绝、缺少响应或 subject drift 都会让任务保持 paused 或 blocked。

<!-- section:isolation -->
## 隔离与最小权限

每个 Run 使用任务 worktree；并行写入者使用独立 worker workspace。只读 Stage 会检查文件系统修改。Capability routing 会先按已认证 sandbox/read-only 要求过滤，再考虑质量或成本。高风险审查应使用指纹不同的 Provider。Network、installation 和 external effect 都必须有明确范围。

<!-- section:evidence -->
## Evidence 与 Fail-Closed 验证

Test 和 Review 必须返回结构化契约。Evidence 记录 claim、command、exit code、artifact、strength、gap 和 risk。缺失或矛盾结果会阻塞 gate。Ledger/event hash 可以暴露修改，却不能让错误观察变真；仍需独立 review 和可复现命令。

<!-- section:signing -->
## 签名与 Attestation

项目 Ed25519 key 位于项目外。任务完成时可创建 canonical 签名 payload，绑定 route、isolation、review、approval、test、Evidence 与 allowlist artifact hash。`.muxattest` bundle 排除私钥和 prompt 材料。验证时要同时检查 integrity、Evidence status、identity pinning 与 warning。未 pin 的 key 只是自声明身份。

<!-- section:secrets -->
## Secret 与私有数据

不要在 task text 中写 token 或 private key。Provider subprocess environment 使用 allowlist；redaction 只是纵深防御，不代表允许摄入 secret。应限制 muxdev home、Provider state、transcript、backup 和 signing key 权限。公开缺陷报告只能包含脱敏复现和 hash，不能包含私有代码或完整 transcript。

<!-- section:reporting -->
## 报告安全问题

优先使用仓库私密安全报告渠道。请提供 muxdev 版本、OS、脱敏步骤、预期/实际边界、受影响 Provider 与 fingerprint，以及相关 event/attestation hash。不要附带 credential、私有仓库内容、signing key、Provider state 或未受控 archive。根级 [SECURITY.md](../../SECURITY.md) 是简明策略入口。
