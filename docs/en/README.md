# muxdev architecture and operations

## Philosophy

muxdev is a thin control plane for complete coding CLIs, not an Agent SDK wrapper. Codex, Claude Code, and similar tools keep ownership of their memory, tools, Skills, MCP integrations, plan mode, and approval protocol. muxdev owns the surrounding facts that require a trusted runtime: Conversations, PTYs, dispatch, isolated worktrees, deterministic merges, Evidence, delivery contracts, and conflict-safe write-back after user acceptance.

Role prompts guide behavior but are not security boundaries. argv-only adapters, environment allowlists, assignment scope, worktree isolation, deterministic checks, and delivery gates are enforced by the Runtime.

One user-scoped Workbench Daemon owns the Web port for every registered local project. `muxdev serve` registers the current directory; if the healthy user instance already exists, it prints that project's `/projects/{project_id}` deep link and exits. The global store contains project registrations, Rules, device authentication, and daemon state only. Conversation, Run, Evidence, ChangeSet, and transcript facts remain in each project's `.muxdev` directory, and all v2 HTTP, SSE, terminal, and file APIs enforce `project_id`.

The built-in Conversation Agent catalog covers Codex, Claude Code, Deep Code, Qwen Code, Kimi Code, Trae, and Antigravity. The Agent Registry scans the daemon `PATH`; the same detected executable drives UI availability, preflight validation, and process launch. Deep Code runs through a real PTY/ConPTY, supports native `--resume <session-id>`, and recognizes `.deepcode/skills` plus `.agents/skills`.

## Execution modes

- `direct` is the default. One primary Agent owns the task end to end. Standard starts an independent Reviewer only when delivery is ready; Strict also requires a Security Reviewer and explicit human confirmation.
- `orchestrated` is opt-in. An Agent with the `orchestrate` capability proposes a validated DAG. The user approves the assignment once, after which up to four ready nodes execute in parallel within the frozen boundaries.
- `legacy_pipeline` preserves the fixed workflows, headless Providers, ACP/MCP, `/api/v1`, and `/runs`.

A Conversation starts in `clarifying`. Questions and answers are Interactions, not Runs. A normal message targets the primary Agent's reusable `main` Session, while `@agent` only joins that Agent to the Conversation. Explicit `consult`, `write`, `review`, or structured primary-Agent dispatch creates an Assignment Run. Retries are Attempts of that same Run; final gating uses a separate `delivery_verification` Run.

The logical Session key is `Conversation × Agent × lane`. Sequential work reuses `main`; concurrent or worktree-incompatible work uses a temporary lane. Process starts, native resumes, and rebuilt context create Session Generations without changing the logical Session ID, terminal entry point, or transcript sequence.

Project schema v13 adds immutable Conversation Memory Checkpoints with source sequence/hash, goals, constraints, human decisions, Assignment results, verification status, and open issues. They are created after Run settlement or once the uncompacted tail exceeds roughly 8,000 tokens. Corrections append new versions. Every Agent receives the same shared checkpoint before its own transcript, Assignment, and dependency output.

## Worktrees and merging

Every parallel write Assignment uses a child worktree. Completion produces a content-addressed ChangeSet. The Runtime merges by topological order and then stable Assignment ID. If a touched path has drifted from the node baseline, the merge fails closed and a conflict-resolution Assignment is created. Each Assignment has at most two safe recovery attempts.

Only the integrated Conversation worktree can become a Delivery Candidate. Sub-agents never write directly into the user's project.

## Persistent Web terminals

Windows prefers ConPTY through `pywinpty`; Unix uses POSIX PTYs and can reattach tmux sessions. Doctor, API, and Web report the actual terminal backend. A `pipe` fallback disables resize and process resume.

Each terminal stores a sequence-numbered local JSONL transcript. A browser attaches read-only by default with `after_seq` for replay and explicitly requests control before obtaining a write lease. Only one device has a write lease; takeover is explicit. `interrupt` sends Ctrl+C/SIGINT to the foreground command without closing the Session. The Dashboard ships local xterm.js and fit-addon assets and provides mobile Esc, Ctrl+C, Tab, arrow, and keyboard-collapse controls.

Remote mode requires a self-managed HTTPS reverse proxy or VPN plus device pairing. WebSockets independently validate the device cookie, Origin, Session ownership, frame size, and rate limits. muxdev provides no cloud relay.

The Dashboard lists paired devices and can revoke them, invalidating their Web sessions. Terminal headers continuously expose connection and write-lease state; explicit takeover replaces another device's active lease.

## Trusted delivery

Users first choose code changes, a specified file, a report/document, a runnable app/API, an analysis answer, or other. The Runtime compiles this concise choice into `muxdev.delivery-standard.v2`, whose requirements contain:

- `deliverable`: what will be delivered;
- `completion`: the deterministic completion condition;
- `proof`: the required proof;
- optional `assignment_id` binding;
- a verifier restricted to `runtime_check`, `agent_review`, `artifact`, or `human_acceptance`.

Runtime checks can only reference frozen argv commands. User text cannot introduce arbitrary shell commands. Changing the standard creates a new DeliveryContract and invalidates prior candidates; accepted deliveries remain immutable.

After all Assignments merge, the Runtime runs deterministic checks and independent reviews against the integrated subject, emits Evidence v3, and creates a candidate only when the gate passes. Acceptance re-verifies the Evidence chain, contract version, and workspace summary before applying the ChangeSet to the user's project.

Immutable `ReviewRecordV1` facts cover interactions, decisions, and change requests with file/line references. Rules can define code standards, CI gates, document templates, or general delivery requirements. They can be built in, user-global, or project-bound and are frozen per Conversation version. CI Rules may only reference registered argv verification commands. Approved Memory Candidates become Rule drafts rather than overwriting project instruction files.

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

Opening an older project database migrates it idempotently to schema v13 and creates a backup before v12→v13. Project data is never moved into the global Workbench store. Unscoped legacy v2 routes are removed; existing v1 APIs, `/runs`, fixed workflows, ACP/MCP, headless Providers, and Evidence reports remain available.

## Relation to botmux

muxdev takes inspiration from botmux's thin orchestration, explicit multi-bot dispatch, and CLI process model. It does not copy botmux code and has no Feishu dependency. Local Conversations, Assignments, and AgentSessions replace chat topics; isolated worktree merging, Evidence, delivery standards, and acceptance-time write-back are muxdev's primary additions.
