# Workflow Delivery Standards

These standards define the minimum delivery bar for muxdev workflow stages. Skills and prompt contracts use the full standard, while lightweight runtime delivery gates enforce only the deterministic subset that can be checked quickly.

## Common Standard

Every completed stage must state:

- Deliverable: the artifact, decision, change, or verification result produced by the stage.
- Decision: pass, block, needs revision, or approved/rejected when the stage is a gate.
- Evidence: commands, files, review findings, approval subject, or references that support the decision.
- Gaps: skipped checks, open questions, deferred work, and residual risk.

Stages must not invent test results, expose secrets, or mix unrelated changes into the delivery claim.

## Stage Standards

| Stages | Required deliverable | Pass when | Block when | Evidence |
| --- | --- | --- | --- | --- |
| `task_intake`, `project_brief` | Problem statement, scope, non-goals, constraints, acceptance criteria, and key assumptions. | The task is actionable and acceptance criteria are verifiable. | The goal is unclear or a missing decision would materially change execution. | Intake summary, assumptions, and open questions. |
| `plan`, `quick_plan`, `scaffold_plan`, `plan_revise` | Implementation plan, ordered steps, impact area, acceptance criteria, verification approach, risks, and deferred work. | Code and test stages can proceed without guessing key decisions. | Steps are not executable, scope is unsafe, or acceptance criteria cannot be verified. | Plan artifact, review feedback addressed, and verification plan. |
| `design`, `design_plan`, `design_brief`, `design_revise`, `design_pack` | Design proposal, interfaces or data flow, tradeoffs, compatibility, migration or rollback risks, and implementation sequence. | The design can support downstream implementation. | Interfaces, data ownership, failure modes, or compatibility constraints are missing. | Design artifact, alternatives considered, and risk mitigations. |
| `approve_plan`, `human_design_approval` | Explicit approval, rejection, or feedback bound to the current plan or design content. | The approved subject has not drifted. | The gate is pending, rejected, or feedback has not been revised into the plan. | Approval record, subject hash, and feedback if present. |
| `implement`, `scaffold`, `code`, `refactor`, `fix` | Runnable code, changed behavior summary, impact area, verification performed, and residual risk. | The implementation satisfies acceptance criteria, follows local patterns, and remains reviewable. | Code does not run, breaks expected behavior, lacks necessary tests, or includes unrelated changes. | Diff, changed files, local run or test results, and noted gaps. |
| `test_strategy` | Verification matrix mapping acceptance criteria to unit, integration, regression, smoke, manual, and risk-based checks. | Every acceptance criterion has a verification path. | Critical behavior has no planned verification. | Test strategy artifact and expected evidence list. |
| `test`, `targeted_test`, `smoke_check`, `run_smoke` | Command or method, result, acceptance criteria covered, failures, and skipped checks. | Relevant checks pass; if the project has coverage configuration, configured coverage thresholds pass. | Relevant checks fail, tests were skipped without a reason, or configured coverage thresholds fail. | Reproducible command output, exit status, coverage result when configured, and skipped-check rationale. |
| `plan_review`, `design_review`, `review`, `light_review`, `impact_check`, `docs_review`, `review_test_result` | Findings ordered by severity, evidence, suggestions, missing evidence, and decision. | No blocking issue remains. | High severity defects, unmet acceptance criteria, missing critical tests, or unresolved safety or compatibility risk remain. | Review result with file or line references when available. |
| `docs_update`, `handoff_summary`, `review_summary` | Accurate reader-facing documentation, commands, behavior changes, and known limitations. | Documentation matches the current implementation and enables the intended reader to proceed. | Documentation is misleading or omits required migration, setup, or run information. | Docs diff, updated commands or examples, and known gaps. |
| `memory_proposals` | Reusable memory item, scope, evidence reference, affected roles, and promotion recommendation. | The claim is stable, non-sensitive, evidence-backed, and reusable. | The proposal contains secrets, personal data, transient state, or unsupported inference. | Source evidence and promotion recommendation. |
| `secure_review` | Threats or findings, severity, affected surfaces, mitigation, and residual risk. | No unmitigated high-risk security or privacy issue remains. | Secrets, authorization, privacy, supply-chain, logging, or input handling risk is high and unmitigated. | Security findings, affected files or surfaces, and mitigations. |

## Testing Standard

Tests must be honest and reproducible. Use the project's existing coverage configuration as the source of truth. If no coverage configuration exists, do not invent a global coverage threshold; instead, provide direct verification for the changed behavior and state any remaining risk.

## Relationship To Evidence

Existing muxdev P/R/E evidence remains the runtime evidence layer. These delivery standards define what each stage should claim and report so evidence, reviews, and handoffs can evaluate the work consistently.

## Lightweight Delivery Gates

`delivery_gate` stages read the active skills for their target stages and use each skill's `## Delivery Standard` as the rule source. At run start, active skill content is frozen into `task_context.json`, including `delivery_rules` and `delivery_rule_hash`, so a single run does not drift if a skill file changes mid-run.

The runtime gate does not call a provider, spend tokens, or require human approval. It writes a `muxdev.delivery_gate.v1` JSON artifact and a `ReviewResult`-compatible role result. Blocking signals are limited to fast local checks: missing or empty stage output, failed tests, open review blockers, runtime errors, pending provider actions, and coverage threshold failures when a project or test output declares one. Natural-language requirements that cannot be determined locally remain warnings or review guidance rather than default hard blocks.

Workflow repair stages such as `plan_revise`, `design_revise`, `fix`, and `docs_fix` point their `loop_review_stage` at the relevant delivery gate. When the gate reports blockers, the workflow retries through the configured repair path until `max_loops` is exhausted; workflows without an automatic repair stage stop as blocked.
