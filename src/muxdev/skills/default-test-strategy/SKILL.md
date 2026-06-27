---
name: default-test-strategy
description: Default muxdev test strategy role skill for dedicated test strategy stages that plan unit, integration, regression, manual, and risk-based validation.
keywords: [test_strategy, validation, coverage, risk]
metadata:
  compatible_roles: [test_strategy]
---
# Default Test Strategy Skill

Use this skill when the stage defines how a change should be verified before execution.

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

## Delivery Standard

- Required deliverable: verification matrix mapping acceptance criteria to unit, integration, regression, smoke, manual, and risk-based checks.
- Pass when every acceptance criterion has a verification path.
- Block when critical behavior has no planned verification.
- Evidence: test strategy artifact, required commands or manual checks, and expected evidence list.
