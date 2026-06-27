---
name: default-test
description: Default muxdev testing role skill for smoke, targeted, and regression verification with reproducible commands and honest missing-evidence reporting.
keywords: [test, verify, qa, regression]
metadata:
  compatible_roles: [test, tester]
---
# Default Test Skill

Use this skill when the stage executes or reports behavior verification.

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

## Delivery Standard

- Required deliverable: command or method, result, acceptance criteria covered, failures, skipped checks, and follow-up recommendation.
- Pass when relevant checks pass and configured project coverage thresholds pass when coverage configuration exists.
- Block when relevant checks fail, tests were skipped without a reason, or configured coverage thresholds fail.
- Evidence: reproducible command output, exit status, coverage result when configured, and skipped-check rationale.
