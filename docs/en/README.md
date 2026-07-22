# muxdev architecture and operations

## Philosophy

muxdev is a thin control plane for complete coding CLIs, not an Agent SDK wrapper. Codex, Claude Code, and similar tools keep ownership of their memory, tools, Skills, MCP integrations, plan mode, and approval protocol. muxdev owns the surrounding facts that require a trusted runtime: Conversations, PTYs, dispatch, isolated worktrees, deterministic merges, Evidence, delivery contracts, and conflict-safe write-back after user acceptance.

Role prompts guide behavior but are not security boundaries. argv-only adapters, environment allowlists, assignment scope, worktree isolation, deterministic checks, and delivery gates are enforced by the Runtime.

## Execution modes

- `direct` is the default. One primary Agent owns the task end to end. Standard starts an independent Reviewer only when delivery is ready; Strict also requires a Security Reviewer and explicit human confirmation.
- `orchestrated` is opt-in. An Agent with the `orchestrate` capability proposes a validated DAG. The user approves the assignment once, after which up to four ready nodes execute in parallel within the frozen boundaries.
- `legacy_pipeline` preserves the fixed workflows, headless Providers, ACP/MCP, `/api/v1`, and `/runs`.

Agents share a Conversation timeline but have isolated CLI contexts, terminals, and worktrees. A normal message targets the primary Agent. `@agent`, `consult`, or `write` explicitly creates cross-Agent collaboration. Agents receive the frozen contract, their own brief, dependency outputs, and directed messages—not the complete transcript by default.

## Worktrees and merging

Every parallel write Assignment uses a child worktree. Completion produces a content-addressed ChangeSet. The Runtime merges by topological order and then stable Assignment ID. If a touched path has drifted from the node baseline, the merge fails closed and a conflict-resolution Assignment is created. Each Assignment has at most two safe recovery attempts.

Only the integrated Conversation worktree can become a Delivery Candidate. Sub-agents never write directly into the user's project.

## Persistent Web terminals

Windows prefers ConPTY through `pywinpty`; Unix uses POSIX PTYs and can reattach tmux sessions. Other CLIs fall back to the existing headless mode with explicit capability reporting.

Each terminal stores a sequence-numbered local JSONL transcript. A browser attaches with `after_seq` for replay. Only one device has a write lease; takeover is explicit. The Dashboard ships local xterm.js and fit-addon assets and provides mobile Esc, Ctrl+C, Tab, arrow, and keyboard-collapse controls.

Remote mode requires a self-managed HTTPS reverse proxy or VPN plus device pairing. WebSockets independently validate the device cookie, Origin, Session ownership, frame size, and rate limits. muxdev provides no cloud relay.

## Trusted delivery

`muxdev.delivery-standard.v2` defines every custom requirement as:

- `deliverable`: what will be delivered;
- `completion`: the deterministic completion condition;
- `proof`: the required proof;
- optional `assignment_id` binding;
- a verifier restricted to `runtime_check`, `agent_review`, `artifact`, or `human_acceptance`.

Runtime checks can only reference frozen argv commands. User text cannot introduce arbitrary shell commands. Changing the standard creates a new DeliveryContract and invalidates prior candidates; accepted deliveries remain immutable.

After all Assignments merge, the Runtime runs deterministic checks and independent reviews against the integrated subject, emits Evidence v3, and creates a candidate only when the gate passes. Acceptance re-verifies the Evidence chain, contract version, and workspace summary before applying the ChangeSet to the user's project.

## Configuration and diagnostics

Configuration precedence remains built-in, user, project `.muxdev/config.yaml`, then `MUXDEV_CONFIG`.

```powershell
muxdev agent list
muxdev agent show codex
muxdev agent doctor
muxdev doctor
muxdev serve
```

CLI adapter commands are argv arrays, never shell strings. The Web UI can select and inspect Agents but cannot edit executable paths or secret environment configuration.

## Migration and compatibility

Opening a v9 database performs an additive v10 migration and marks existing Conversations as `legacy_pipeline`. v10 adds `agent_sessions`, `orchestration_plans`, `assignments`, and `assignment_dependencies`. Existing v1 APIs, fixed workflows, ACP/MCP, headless Providers, and Evidence reports remain available.

## Relation to botmux

muxdev takes inspiration from botmux's thin orchestration, explicit multi-bot dispatch, and CLI process model. It does not copy botmux code and has no Feishu dependency. Local Conversations, Assignments, and AgentSessions replace chat topics; isolated worktree merging, Evidence, delivery standards, and acceptance-time write-back are muxdev's primary additions.
