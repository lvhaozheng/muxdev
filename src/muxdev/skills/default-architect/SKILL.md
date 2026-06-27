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

- Complete single-file design document
- Problem, scope, users, platform, constraints, and non-goals
- Proposed design, interactions, UI/state model, rules, and data model
- Acceptance criteria, test strategy, risks, implementation roadmap, and open questions
- Important alternatives only when the choice affects cost, risk, or reversibility

## Design Document Standard

A user-facing design document must be complete enough for a downstream implementer to work without hidden decisions. For lightweight games or browser experiences, cover the gameplay loop, controls, scoring, failure conditions, screens, state transitions, responsive/mobile input, visual direction, acceptance criteria, test strategy, implementation roadmap, risks, and open questions. Keep it concise, but do not replace these sections with a short summary.

Do not treat "no implementation files were created", "read-only stage", or "not verified by running code" as acceptance criteria. Those can be scope notes or residual risks, but the acceptance criteria must describe observable design quality or future implementation behavior.

## Delivery Standard

- Required deliverable: complete single-file design document with goal/scope, users/platform, interaction or system design, UI/states, rules/data, acceptance criteria, test strategy, risks, implementation roadmap, and open questions.
- Pass when the design can support downstream implementation without hidden product, architecture, state, data, testing, or acceptance decisions.
- Block when the design is only a summary, lacks key sections, uses non-delivery facts as acceptance criteria, or omits interfaces, data ownership, failure modes, compatibility constraints, or test strategy.
- Evidence: design document, alternatives considered when relevant, affected surfaces, state/data model, acceptance criteria, test strategy, and risk mitigations.
