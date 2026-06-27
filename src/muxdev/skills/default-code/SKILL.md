---
name: default-code
description: Default muxdev coding role skill for implementation, fixes, scaffolding, and refactors that follow local project patterns and preserve user changes.
keywords: [code, implement, refactor, fix]
metadata:
  compatible_roles: [code, implementer]
---
# Default Code Skill

Use this skill when the stage is allowed to modify workspace files.

## Operating Rules

- Read the surrounding code before editing.
- Follow existing architecture, naming, formatting, and test patterns.
- Keep changes scoped to the task and avoid unrelated cleanup.
- Preserve user changes and generated artifacts that are unrelated to the task.
- Prefer structured parsers and local helper APIs over brittle string edits.
- Update tests or docs when behavior changes and the workflow has not delegated that work elsewhere.

## Output Shape

- Files changed
- Behavior changed
- Verification performed
- Remaining risk

## Delivery Standard

- Required deliverable: runnable code, changed behavior summary, impact area, verification performed, and residual risk.
- Pass when the implementation satisfies acceptance criteria, follows local patterns, and remains reviewable.
- Block when code does not run, breaks expected behavior, lacks necessary tests, or includes unrelated changes.
- Evidence: diff, changed files, local run or test results, and noted gaps.
