---
name: default-review
description: Default muxdev review role skill for plan, design, implementation, test, and docs review stages that find bugs, regressions, safety issues, missing tests, and contract drift.
keywords: [review, blocker, regression, correctness]
metadata:
  compatible_roles: [review, reviewer]
---
# Default Review Skill

Use this skill when the stage reviews a plan, design, patch, test result, documentation update, or delivery claim.

## Operating Rules

- Lead with actionable findings ordered by severity.
- Focus on correctness, regressions, missing tests, safety, and maintainability.
- Include file and line references when available.
- Treat missing evidence as a risk, not as proof of failure.
- If there are no blockers, say so clearly and name residual risk.
- For design reviews, block shallow summaries that do not cover goal/scope, users/platform, interactions or system design, UI/states, rules/data, acceptance criteria, test strategy, risks, roadmap, and open questions.
- For design reviews, block acceptance criteria that merely say no implementation files were created, the stage was read-only, or runtime verification did not happen.

## Output Shape

- Blockers
- Non-blocking concerns
- Missing evidence
- Decision

## Delivery Standard

- Required deliverable: findings ordered by severity, evidence, suggestions, missing evidence, decision, and residual risk.
- Pass when no blocking issue remains and design documents meet the complete single-file design standard when reviewing design stages.
- Block when high severity defects, unmet acceptance criteria, missing critical tests, incomplete design documents, shallow summaries, non-delivery acceptance criteria, or unresolved safety or compatibility risk remain.
- Evidence: review result with file or line references when available, plus design-document coverage evidence for design reviews.
