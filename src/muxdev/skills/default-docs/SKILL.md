---
name: default-docs
description: Default muxdev documentation role skill for docs updates, design packs, review summaries, handoff summaries, and accurate user-facing or maintainer-facing documentation.
keywords: [docs, documentation, readme, guide]
metadata:
  compatible_roles: [docs]
---
# Default Docs Skill

Use this skill when the stage writes documentation, a design pack, a handoff summary, or a review summary.

## Operating Rules

- Write for the reader's task, not for the implementation history.
- Keep examples runnable and aligned with current commands or APIs.
- Update nearby docs when behavior, configuration, or workflow names change.
- Avoid documenting speculative future behavior as available.
- Prefer concise headings, concrete steps, and visible limitations.

## Output Shape

- Docs changed
- Behavior documented
- Examples or commands updated
- Known gaps

## Delivery Standard

- Required deliverable: accurate reader-facing documentation, commands, behavior changes, and known limitations.
- Pass when documentation matches the current implementation and enables the intended reader to proceed.
- Block when documentation is misleading or omits required migration, setup, or run information.
- Evidence: docs diff, updated commands or examples, and known gaps.
