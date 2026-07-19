# Development

[中文](../cn/development.md)

<!-- section:setup -->
## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
ruff check .
python scripts/verify_docs.py
pytest -q -m "not release"
```

Keep existing uncommitted work unless it is explicitly in scope. Use small patches, preserve v7 data compatibility, and test the narrowest affected boundary first.

<!-- section:layout -->
## Source Layout

- `domain`: stable typed contracts and state rules.
- `application`: lifecycle, command, query, and coordination use cases.
- `runtime`: native scheduling, Stage execution, recovery, worktrees, and merge.
- `providers`: typed adapters, parsers, certification, and Provider policy.
- `storage`: SQLite, migrations, events, repositories, read models, and files.
- `daemon`: durable workers, task backend, process lifecycle, and event bus.
- `services`: bounded product capabilities such as Evidence, routing, RAG, and attestation.
- `cli`, `api`, `presentation`: entry and rendering surfaces.

<!-- section:tests -->
## Test Strategy

Use unit tests for pure contracts, integration tests for SQLite/runtime/API, and release tests only for explicit packaging or live gates. Required local checks are Ruff, bilingual documentation validation, targeted tests, non-release suite, and a cross-platform quick suite. Runtime tests must cover sequential/parallel equivalence, invalid structured results, read-only violations, retries, Provider Action pause/resume, lease fencing, conflicts, and final delivery.

<!-- section:architecture-rules -->
## Architecture Rules

Dependencies point inward. Domain never imports an outer layer. API/CLI handlers validate/translate and call application services; they do not orchestrate Blackboard or Runtime. Provider code does not import daemon or UI. Presentation is read-only. All Provider execution uses `StageExecutionInput` and `StageExecutionResult`; do not add `run_stage(**kwargs)` compatibility or inspect callable signatures.

<!-- section:provider-extension -->
## Add a Provider

Implement the typed adapter, Provider-specific event parser, static probe, certification evidence, cancellation behavior, and optional native resume template. Keep subprocess environment allowlisted and transcripts in the controlled session directory. Add contract, failure classification, stale fingerprint, certification, and offline fixture tests. Never infer capabilities from brand name alone.

<!-- section:workflow-extension -->
## Add a Workflow or Skill

Workflow stages need stable IDs, roles, dependencies, write/read-only policy, schemas, delivery targets, and bounded loop metadata. Verify the exported native DAG and required deliverables. Skills need valid metadata, explicit trust, scoped role/stage binding, lock verification, and tests showing untrusted content is not auto-selected.

<!-- section:migration-extension -->
## Change Storage

Add an ordered, checksummed migration; never edit an applied migration. Keep event and projection changes atomic, idempotent, replayable, and fenced. Add a frozen pre-change fixture plus query, Evidence, resume, backup/restore, and concurrent ownership tests. Do not reset user databases during upgrade.

<!-- section:docs-extension -->
## Change Documentation

`docs/en` and `docs/cn` must contain exactly the same eight filenames and the same ordered `<!-- section:id -->` markers. Information must be equivalent, while prose need not be sentence-for-sentence translation. All relative links, source paths, and counterpart links must resolve. Do not add loose files under `docs`.

<!-- section:build-install -->
## Build and Install Smoke Test

```powershell
python -m build
python -m venv .wheel-smoke
.\.wheel-smoke\Scripts\python -m pip install dist\*.whl
.\.wheel-smoke\Scripts\muxdev --version
.\.wheel-smoke\Scripts\muxdev graph export --json
```

Build directories are disposable release artifacts; source, docs, databases, and user runs are not. Verify the wheel in a clean environment before tagging.
