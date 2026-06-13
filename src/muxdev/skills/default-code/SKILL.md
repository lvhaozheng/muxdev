---
name: default-code
description: Default muxdev coding role skill for scoped implementation that follows local project patterns and preserves user changes.
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
- Update tests or docs when behavior changes and risk justifies it.

## Output Shape

- Files changed
- Behavior changed
- Verification performed
- Remaining risk
