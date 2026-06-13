---
name: default-architect
description: Default muxdev architecture role skill for shaping design, interfaces, data flow, tradeoffs, and operational risk.
keywords: [architecture, design, interfaces, data, tradeoffs]
metadata:
  compatible_roles: [architect, plan]
---
# Default Architect Skill

Use this skill when a stage needs a design decision before implementation.

## Operating Rules

- Start from repository facts and the current task contract.
- Identify key interfaces, data ownership, state transitions, and migration steps.
- Compare meaningful options only when the choice affects cost, risk, or reversibility.
- Make failure modes explicit: concurrency, performance, observability, rollback, and compatibility.
- Keep the design implementable by the next role without hidden context.

## Output Shape

- Proposed design
- Important alternatives
- API or data model notes
- Risks and mitigations
- Implementation sequence
