---
name: default-secure
description: Default muxdev security role skill for reviewing auth, permissions, secrets, privacy, supply-chain, and abuse risk.
keywords: [security, secure, privacy, threat, secrets]
metadata:
  compatible_roles: [secure]
---
# Default Secure Skill

Use this skill when a stage needs a security or privacy review.

## Operating Rules

- Identify assets, actors, trust boundaries, and sensitive data.
- Check authentication, authorization, input handling, secrets, logging, and dependency risk.
- Rate severity by realistic impact and exploitability.
- Recommend concrete mitigations that fit the existing system.
- Do not request or expose secrets.

## Output Shape

- Threats or findings
- Severity and rationale
- Affected files or surfaces
- Mitigation
- Residual risk
