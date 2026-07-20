---
name: default-test
description: Plan and execute reproducible verification. Use for test planning, targeted tests, smoke checks, regressions, and acceptance-criteria validation.
---

# Verify behavior

- Map acceptance criteria to the smallest relevant deterministic checks first.
- Expand to integration, migration, compatibility, negative, or manual checks when risk requires it.
- Provide each executable check as an argv vector that the runtime can replay without a shell.
- Distinguish passed, failed, skipped, and unavailable checks; explain every skip.
- Identify whether a failure is related to the delivered change without hiding unrelated failures.

Return the workflow's declared `TestResult`. Report what you observed, but the runtime replay is authoritative for command arguments, output digests, exit codes, and `CheckEvidence`.
