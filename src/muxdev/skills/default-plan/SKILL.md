---
name: default-plan
description: Default muxdev planning role skill for implementation plans, scaffold plans, plan revisions, acceptance criteria, risks, and handoff to coding and verification stages.
metadata:
  compatible_roles: [plan, architect]
  tags: [plan, planning, implementation, scaffold, handoff]
---
# Default Plan Skill

Use this skill when the stage turns requirements or review feedback into an implementation-ready plan.

## Operating Rules

- Ground the plan in repository facts, the task contract, and prior stage outputs.
- Separate decisions, assumptions, acceptance criteria, risks, and deferred scope.
- Keep steps ordered so the code and test stages can execute without hidden context.
- Prefer reversible implementation choices and small checkpoints.
- When revising, state what changed because of review blockers or user plan feedback.

## Output Shape

- Summary
- Ordered implementation steps
- Acceptance criteria
- Verification approach
- Risks, assumptions, and deferred work

## Delivery Standard

- Required deliverable: implementation plan, ordered steps, impact area, acceptance criteria, verification approach, risks, and deferred work.
- Pass when code and test stages can proceed without guessing key decisions.
- Block when steps are not executable, scope is unsafe, or acceptance criteria cannot be verified.
- Evidence: plan artifact, repository facts used, review feedback addressed, and verification plan.
