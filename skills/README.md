# muxdev Skills

This directory documents the builtin/exportable skill area for muxdev. Bundled
runtime skills live under `src/muxdev/skills` so they are shipped with the
Python package. Project teams can copy any bundled role skill into
`.muxdev/skills/<name>/` or `skills/<name>/` and customize it without changing
source code.

Skills are treated as governed `SKILL.md` packages instead of provider CLI
wrappers.

## Package Shape

```text
my-skill/
|-- SKILL.md
|-- muxdev.skill.toml
|-- scripts/
|-- references/
|-- assets/
`-- evals/
```

`SKILL.md` is the ecosystem-compatible entrypoint and must carry at least a
`name` and useful `description`. `muxdev.skill.toml` is optional and only stores
muxdev policy such as activation roles, stages, file patterns, permissions,
trust, and evidence requirements.

## Progressive Loading

`muxdev skill catalog` returns Level 1 metadata only: name, description, path,
roles, trust, risk, and permissions. It never includes the full instructions.

`muxdev skill activate <name>` loads the full `SKILL.md` on demand and records a
`skill_activation` event in `.muxdev/skill-events.jsonl`.

Resources under `scripts/`, `references/`, and `assets/` are listed only when
activation requests `--resources`; scripts are still data until an approval and
sandbox path explicitly executes them.

## Governance Commands

```powershell
muxdev skill catalog --role review --json
muxdev skill explain --task "review auth changes" --role review --json
muxdev skill activate secure-review --role review --provider codex --json
muxdev skill trust secure-review project_trusted --scope project
muxdev skill quarantine secure-review --reason "lock drift"
muxdev skill lock --no-memory --json
muxdev skill verify --lock --json
muxdev skill eval secure-review --provider mock --json
muxdev skill score secure-review --last 30d --json
```

`skill-lock.json` uses `muxdev.skill_lock.v2` and hashes the whole skill tree,
including `scripts/`, `references/`, and `assets/`. Changing any locked resource
after lock generation makes `muxdev skill verify --lock` fail.

## Builtin Role Skills

`muxdev` ships default role skills for requirements, architect, code, test,
test_strategy, review, secure, docs, and memory_curator. They are
`builtin_trusted`, auto-selected for matching workflow roles, and can be
overridden by higher-priority project skills with the same name.

To customize a role for a business workflow:

```powershell
mkdir .muxdev\skills\default-code
copy src\muxdev\skills\default-code\SKILL.md .muxdev\skills\default-code\SKILL.md
copy src\muxdev\skills\default-code\muxdev.skill.toml .muxdev\skills\default-code\muxdev.skill.toml
muxdev skill catalog --role code --json
```
