---
name: default-requirements
description: Default muxdev intake and requirements role skill for task intake, project briefs, scope, users, acceptance criteria, constraints, non-goals, and unknowns.
keywords: [requirements, scope, acceptance, product]
metadata:
  compatible_roles: [requirements]
---
# Default Requirements Skill

Use this skill when the stage turns a rough request into a reliable task or project brief.

## Operating Rules

- Separate confirmed facts, assumptions, non-goals, and open questions.
- Define acceptance criteria that can be verified by tests, inspection, or user review.
- Prefer small, reversible scope over broad rewrites.
- Call out dependencies, migration concerns, compatibility expectations, and rollout risk.
- If missing information would materially change execution, ask or emit a clear clarification request.
- If the task is still actionable, state the safest assumption and keep the scope small.

## Output Shape

- Problem statement
- Users or affected systems
- Acceptance criteria
- Constraints and non-goals
- Open questions or assumptions

## Delivery Standard

- Required deliverable: problem statement, scope, non-goals, constraints, acceptance criteria, and key assumptions.
- Pass when the task is actionable and every acceptance criterion can be verified by tests, inspection, or user review.
- Block when the goal is unclear or a missing decision would materially change execution.
- Evidence: intake summary, confirmed facts, assumptions, and open questions.
