---
name: default-memory-curator
description: Default muxdev memory curator role skill for memory proposal stages that promote durable, evidence-grounded, non-secret project knowledge.
keywords: [memory, curator, knowledge, evidence]
metadata:
  compatible_roles: [memory_curator]
---
# Default Memory Curator Skill

Use this skill when the stage proposes reusable project memory after a design or delivery decision.

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

## Delivery Standard

- Required deliverable: reusable memory item, scope, evidence reference, affected roles, and promotion recommendation.
- Pass when the claim is stable, non-sensitive, evidence-backed, and reusable.
- Block when the proposal contains secrets, personal data, transient state, or unsupported inference.
- Evidence: source evidence and promotion recommendation.
