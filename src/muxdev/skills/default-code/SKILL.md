---
name: default-code
description: Implement scoped code or documentation changes. Use for implementation, fixes, refactors, scaffolding, and documentation stages that may modify the workspace.
---

# Implement changes

- Inspect surrounding code and preserve unrelated user changes.
- Make the smallest coherent change that satisfies the accepted plan or task.
- Follow existing naming, typing, formatting, and test patterns.
- Avoid unrelated cleanup and generated-file edits unless required.
- Update tests or documentation when behavior changes and no later stage owns that work.
- Report the changed behavior, affected paths, suggested verification, and residual risks.

Return the workflow's declared `ChangeResult`. Declared paths and checks are hints only; the runtime computes the authoritative diff, artifact digests, and verification records.
