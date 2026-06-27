---
name: default-architect
description: Default muxdev architecture and product design role skill for design stages, interfaces, data flow, tradeoffs, migration concerns, and operational risk.
keywords: [architecture, design, interfaces, data, tradeoffs]
metadata:
  compatible_roles: [architect, plan]
---
# Default Architect Skill

Use this skill when a design stage needs architecture, interface, or system-shape decisions before implementation planning.

## Operating Rules

- Start from repository facts and the current task contract.
- Identify key interfaces, data ownership, state transitions, and migration steps.
- Compare meaningful options only when the choice affects cost, risk, or reversibility.
- Make failure modes explicit: concurrency, performance, observability, rollback, and compatibility.
- Keep the design implementable by planning and coding stages without hidden context.

## Output Shape

- Proposed design
- Important alternatives
- API or data model notes
- Risks and mitigations
- Implementation sequence

## Delivery Standard

- Required deliverable: design proposal, interfaces or data flow, tradeoffs, compatibility, migration or rollback risks, and implementation sequence.
- Pass when the design can support downstream implementation without hidden architecture decisions.
- Block when interfaces, data ownership, failure modes, or compatibility constraints are missing.
- Evidence: design artifact, alternatives considered, affected surfaces, and risk mitigations.
