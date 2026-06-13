---
name: default-test
description: Default muxdev testing role skill for targeted verification, reproducible commands, and honest missing-evidence reporting.
keywords: [test, verify, qa, regression]
metadata:
  compatible_roles: [test, tester]
---
# Default Test Skill

Use this skill when the stage validates behavior or quality.

## Operating Rules

- Choose the smallest test set that covers the changed behavior first.
- Expand to broader regression checks when the change touches shared contracts.
- Report exact commands, exit codes, and relevant output summaries.
- Distinguish passed checks, failed checks, skipped checks, and checks that could not be run.
- When tests fail, identify whether the failure appears task-related.

## Output Shape

- Command or method
- Result
- Coverage of acceptance criteria
- Missing evidence
- Follow-up recommendation
