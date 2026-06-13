# Ecosystem And Automation Readiness

This guide validates muxdev ecosystem automation after the extension model was
consolidated to skills-first governance.

## What To Verify

- Feedback Router records CI, review, issue, manual, and security feedback.
- CI Rescue can turn failed feedback into a follow-up daemon task.
- CAS Cache writes reusable feedback/result records under `.muxdev/cache/cas/...`.
- Skill Lock writes `.muxdev/skill-lock.json` with whole-tree hashes, versions, compatible roles, and status.
- MCP exposes local muxdev tools, resources, and prompts for external agents.
- MCP Guardrail records denied or audited tool calls.
- Dashboard shows MCP as a compact status strip: mode, tool/resource/prompt counts, guarded write policy, and recent guardrails.
- Dashboard task detail shows timeline, evidence, memory, role sessions, feedback, CI rescue, cache, skill lock, and guardrail events.

## Extension Model

Skills are the maintained extension unit. Use `muxdev skill ...` for reusable
agent behavior, trust policy, lock files, evaluation, and scorecards. MCP is the
external interoperability boundary for tools/resources/prompts. The Dashboard
does not manage MCP servers; it only shows lightweight status and guardrails.
muxdev can reintroduce a plugin registry only if a future runtime packages
skills, hooks, MCP servers, and commands together.

## Persistent Tables

```text
feedback_events
ci_rescues
cache_entries
skill_locks
guardrail_events
```

Historical `plugin_manifests` tables may remain in old local databases, but new
muxdev commands no longer write or display plugin manifest records.

## Quick Validation

```powershell
python -m pip install -e ".[test]"
muxdev setup --project --yes
muxdev serve --restart
```

```powershell
muxdev feedback add ci_failed "pytest failed in tests/test_login.py" --source github-actions --provider mock --json
muxdev feedback list --json
muxdev cache list --json
muxdev ci rescue "npm test failed on auth flow" --source github-actions --provider mock --json
```

```powershell
muxdev skill catalog --role review --json
muxdev skill explain --task "review auth changes" --role review --json
muxdev skill trust secure-review project_trusted --scope project
muxdev skill lock --no-memory --json
muxdev skill verify --lock --json
```

```powershell
muxdev mcp manifest --json
muxdev mcp doctor --json
muxdev mcp serve --stdio
muxdev mcp serve --request '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"muxdev.check_policy","arguments":{"command":"rm -rf /"}}}'
```

## Regression

```powershell
python -B -m pytest -q -k "feedback_router or skill_lock or mcp_guardrail or dashboard_renders"
python -B -m pytest -q
```
