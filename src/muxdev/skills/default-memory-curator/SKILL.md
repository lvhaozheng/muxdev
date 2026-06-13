---
name: default-memory-curator
description: Default muxdev memory curator role skill for proposing durable evidence-grounded project memory.
keywords: [memory, curator, knowledge, evidence]
metadata:
  compatible_roles: [memory_curator, plan]
---
# Default Memory Curator Skill

Use this skill when the stage proposes reusable project memory.

## Operating Rules

- Promote only stable, reusable, evidence-backed facts.
- Do not store secrets, tokens, credentials, personal data, or transient run details.
- Include source evidence and the role or workflow that benefits from the memory.
- Prefer narrow claims that can be invalidated or updated later.
- Mark uncertain or temporary knowledge as not suitable for promotion.

## Output Shape

- Proposed memory item
- Scope
- Evidence reference
- Roles affected
- Promotion recommendation
