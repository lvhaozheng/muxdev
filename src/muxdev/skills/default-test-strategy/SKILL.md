---
name: default-test-strategy
description: Default muxdev test strategy role skill for planning unit, integration, regression, manual, and risk-based validation.
keywords: [test_strategy, validation, coverage, risk]
metadata:
  compatible_roles: [test_strategy, test, tester]
---
# Default Test Strategy Skill

Use this skill when the stage defines how a change should be verified.

## Operating Rules

- Map acceptance criteria to concrete verification methods.
- Prefer fast deterministic checks for core behavior.
- Add integration or manual checks for cross-boundary workflows.
- Include negative, compatibility, rollback, and migration cases when relevant.
- Name the evidence expected from the later test role.

## Output Shape

- Verification matrix
- Required commands or manual checks
- Risk-based additional checks
- Evidence expected
