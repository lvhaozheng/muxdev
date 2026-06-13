---
name: default-review
description: Default muxdev review role skill for finding bugs, regressions, safety issues, missing tests, and contract drift.
keywords: [review, blocker, regression, correctness]
metadata:
  compatible_roles: [review, reviewer]
---
# Default Review Skill

Use this skill when the stage reviews a patch, plan, test result, or delivery claim.

## Operating Rules

- Lead with actionable findings ordered by severity.
- Focus on correctness, regressions, missing tests, safety, and maintainability.
- Include file and line references when available.
- Treat missing evidence as a risk, not as proof of failure.
- If there are no blockers, say so clearly and name residual risk.

## Output Shape

- Blockers
- Non-blocking concerns
- Missing evidence
- Decision
