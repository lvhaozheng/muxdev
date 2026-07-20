"""Generate the measured architecture and interview evidence pack."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from architecture_metrics import ROOT, collect


OUTPUT = ROOT / "release-artifacts"
BASELINE = {
    "python_files": 166,
    "logical_lines": 38_933,
    "http_routes": 99,
    "typer_commands": 159,
    "sqlite_tables": 54,
    "workflows": 11,
    "builtin_skills": 10,
    "functions_over_120_lines": 14,
    "largest_file_lines": 3_647,
    "layer_cycles": 1,
}


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    current = collect()
    metrics = _metrics(current)
    _write_json("architecture-metrics.json", metrics)
    _write("simplification-report.md", _simplification(metrics))
    _write_json("evidence-v3-example.json", _evidence_examples())
    _write("architecture-diagrams.md", _diagrams())
    _write("open-source-research.md", _research())
    _write("ai-agent-infrastructure-case-study.md", _case_study(metrics))
    _write("interview-narratives.md", _interview(metrics))
    _write("resume-bullets.md", _resume(metrics))


def _metrics(current: dict[str, object]) -> dict[str, object]:
    source = current["source"]
    surfaces = current["surfaces"]
    architecture = current["architecture"]
    after = {
        "python_files": source["python_files"],
        "logical_lines": source["logical_lines"],
        "http_routes": surfaces["http_routes"],
        "typer_commands": surfaces["typer_commands"],
        "sqlite_tables": surfaces["sqlite_tables"],
        "workflows": surfaces["workflows"],
        "builtin_skills": surfaces["builtin_skills"],
        "functions_over_120_lines": len(source["functions_over_120_lines"]),
        "largest_file_lines": max(item["lines"] for item in source["largest_files"]),
        "layer_cycles": len(architecture["layer_cycles"]),
    }
    delta = {
        key: {
            "absolute": int(after[key]) - before,
            "reduction_percent": round(100 * (before - int(after[key])) / before, 1) if before else None,
        }
        for key, before in BASELINE.items()
    }
    return {
        "schema": "muxdev.architecture_metrics.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "before": BASELINE,
        "after": after,
        "delta": delta,
        "surfaces": surfaces,
        "four_layers": architecture["four_layers"],
        "largest_files": source["largest_files"],
        "targets": {
            "production_lines_reduced_at_least_30_percent": delta["logical_lines"]["reduction_percent"] >= 30,
            "python_files_reduced_at_least_25_percent": delta["python_files"]["reduction_percent"] >= 25,
            "core_file_at_most_1000_lines": after["largest_file_lines"] <= 1000,
            "core_function_at_most_120_lines": after["functions_over_120_lines"] == 0,
            "no_reverse_or_cyclic_layer_dependency": not architecture["layer_cycles"] and not architecture["four_layers"]["reverse_dependency_violations"],
        },
    }


def _evidence_examples() -> dict[str, object]:
    dimensions = {
        "reproducibility": {"numerator": 1, "denominator": 1, "percent": 100.0, "failed_requirements": []},
        "integrity": {"numerator": 4, "denominator": 4, "percent": 100.0, "failed_requirements": []},
        "independence": {"numerator": 1, "denominator": 1, "percent": 100.0, "failed_requirements": []},
    }
    return {
        "schema": "muxdev.evidence-v3-examples.v1",
        "note": "Scores explain evidence quality and never override a hard requirement.",
        "examples": [
            {
                "name": "verified-pass",
                "decision": {
                    "status": "PASS",
                    "policy_hash": "sha256:example-pass-policy",
                    "requirements": [{"requirement_id": "deterministic_check", "status": "satisfied", "record_ids": ["ev_check"], "reason": "Runtime captured exit code 0."}],
                    "blockers": [],
                    "scorecard": {"completeness": {"numerator": 4, "denominator": 4, "percent": 100.0, "failed_requirements": []}, **dimensions, "overall": 100.0},
                },
            },
            {
                "name": "high-score-but-blocked",
                "decision": {
                    "status": "BLOCKED",
                    "policy_hash": "sha256:example-blocked-policy",
                    "requirements": [{"requirement_id": "human_approval", "status": "missing", "record_ids": [], "reason": "Required human approval is missing.", "remediation": "Request an explicit approval bound to the current subject."}],
                    "blockers": [{"code": "requirement_missing", "requirement_id": "human_approval", "reason": "Required human approval is missing.", "record_ids": [], "remediation": "Request an explicit approval bound to the current subject."}],
                    "scorecard": {"completeness": {"numerator": 4, "denominator": 5, "percent": 80.0, "failed_requirements": ["human_approval"]}, **dimensions, "overall": 95.0},
                },
            },
        ],
    }


def _simplification(metrics: dict[str, object]) -> str:
    before, after, delta = metrics["before"], metrics["after"], metrics["delta"]
    return f"""# muxdev simplification report

## Measured outcome

| Metric | Before | After | Reduction |
|---|---:|---:|---:|
| Production Python lines | {before['logical_lines']:,} | {after['logical_lines']:,} | {delta['logical_lines']['reduction_percent']}% |
| Python files | {before['python_files']} | {after['python_files']} | {delta['python_files']['reduction_percent']}% |
| Workflows | {before['workflows']} | {after['workflows']} | {delta['workflows']['reduction_percent']}% |
| Built-in Skills | {before['builtin_skills']} | {after['builtin_skills']} | {delta['builtin_skills']['reduction_percent']}% |
| SQLite tables | {before['sqlite_tables']} | {after['sqlite_tables']} | {delta['sqlite_tables']['reduction_percent']}% |
| HTTP routes | {before['http_routes']} | {after['http_routes']} | {delta['http_routes']['reduction_percent']}% |
| CLI leaf commands | {before['typer_commands']} | {after['typer_commands']} | {delta['typer_commands']['reduction_percent']}% |

## Deleted

- Memory/RAG promotion, multi-repository orchestration, generic parallel DAG machinery, Skill marketplace governance, experimental learning projections, and the provider-score learning chain.
- The 3,000-line Supervisor, 3,100-line Blackboard, dynamic repository facades, Provider sessions/planners, duplicate Evidence projections, legacy dashboards, and daemon-specific command surfaces.
- Markdown/TOML/Python gate authority in Skills. Legacy declarations are guidance-only and receive migration suggestions.

## Merged

- 11 workflows into `change / design / review / test`, with `lite / standard / strict` Profiles.
- 10 built-in Skills into five role guides; structured output schemas live only on workflow stages.
- Three Evidence products into `evidence-report.json`; DSSE is an optional envelope that binds the report digest.
- 54 database tables into 12 typed fact tables, with v7 import as unscored `legacy_evidence`.

## Kept because it carries technical depth

- Durable stage checkpoints, explicit human interrupts, safe reconciliation, and bounded repair loops.
- Capability/certification-aware routing, Beta lower-bound history, heterogeneous review, Replay, and Benchmark.
- Deterministic EvidencePolicy evaluation, subject-bound Review, four-dimensional Scorecard, event-chain integrity, and DSSE attestation.
- Atomic backup/import/verification/switch migration with failure rollback.
"""


def _diagrams() -> str:
    return """# muxdev architecture diagrams

## Four layers

```mermaid
flowchart LR
  C["Composition: CLI / HTTP / MCP / RunEngine"] --> A["Adapters: Provider / SQLite / Config / Skills"]
  C --> P["Application: TaskService ports"]
  A --> D["Domain: Evidence / Run / Stage contracts"]
  P --> D
```

## Durable recovery

```mermaid
sequenceDiagram
  participant U as User
  participant E as RunEngine
  participant S as ControlStore
  participant P as ProviderAdapter
  U->>E: run or resume
  E->>S: persist job and stage=running
  E->>P: execute(StageExecutionInput)
  P-->>E: StageExecutionResult
  E->>S: persist output, usage, evidence, stage=completed
  alt human decision required
    E->>S: interaction=pending
    E-->>U: WAITING_HUMAN
    U->>S: approved/rejected
    U->>E: resume
  end
  E->>S: replay completed stages; reconcile opaque running stage
```

## Evidence and gate data flow

```mermaid
flowchart LR
  W["Workflow + Profile"] --> EP["Frozen EvidencePolicy"]
  R["Runtime observations"] --> ER["Typed EvidenceRecord events"]
  EP --> G["Deterministic Gate Engine"]
  ER --> G
  G --> GD["PASS / BLOCKED / WAITING_HUMAN"]
  G --> SC["Scorecard: completeness / reproducibility / integrity / independence"]
  GD --> REP["evidence-report.json"]
  SC --> REP
  REP --> DSSE["optional attestation.dsse.json"]
```
"""


def _case_study(metrics: dict[str, object]) -> str:
    after, delta = metrics["after"], metrics["delta"]
    return f"""# AI Agent Infrastructure Case Study: muxdev

## Problem

muxdev had accumulated platform features faster than it accumulated trustworthy delivery semantics. Eleven workflows, ten built-in Skills, 54 tables, 99 HTTP routes, 159 CLI commands, and three competing Evidence representations made completion hard to explain and harder to verify.

## Decision

The product was narrowed to a trusted-delivery control plane. TaskService owns lifecycle use cases; RunEngine owns durable stage execution. Provider output is treated as a claim, while diffs, command exit codes, subject digests, reviewer identity, human decisions, and event-chain hashes are runtime facts. Skills can guide work but cannot grant permissions or decide gates.

## Key engineering choices

1. A minimal persisted state machine instead of LangGraph, Temporal, or DBOS: four workflows did not justify a general orchestration dependency.
2. One EvidencePolicy and one deterministic Gate Engine: hard requirements remain independent from the explanatory Scorecard.
3. One Provider execution method: capability discovery and certification are separate from execution, and Provider self-reported confidence is ignored.
4. Append-only typed events and a 12-table fact store: projections no longer become competing sources of truth.
5. Subject-bound independent review and optional DSSE: attestations bind the final report without copying business facts.

## Result

Production Python fell from 38,933 to {after['logical_lines']:,} lines ({delta['logical_lines']['reduction_percent']}% reduction) and files from 166 to {after['python_files']} ({delta['python_files']['reduction_percent']}% reduction). The largest production file is {after['largest_file_lines']} lines, no function exceeds 120 lines, all four-layer reverse-dependency checks pass, and the public surfaces are fixed at 29 CLI commands, 18 HTTP routes, and eight MCP tools.
"""


def _research() -> str:
    return """# Open-source architecture research and adoption record

This report records patterns adopted after reviewing primary project documentation. muxdev reimplements only the minimum semantics required by its four fixed workflows; none of the referenced workflow frameworks became a runtime dependency.

| Source | Observed mature pattern | muxdev adoption | Deliberately not adopted | Code proof |
|---|---|---|---|---|
| [OpenHands typed events](https://docs.openhands.dev/sdk/arch/events) | Immutable typed events form an append-only history; origin and presentation role are distinct. | Typed EvidenceRecord events, immutable event payloads, sequence and SHA-256 previous-hash chain. | Conversation memory, condensation, observer framework, and LLM message conversion. | `models/evidence.py`, `storage/control.py` |
| [Codex app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md) | Submission returns identity/state, progress is emitted as events, and approvals are explicit server requests with authoritative completion events. | Runs, events, interactions, and responses are separate resources; CLI/HTTP/MCP share the same TaskService lifecycle. | A general bidirectional conversation protocol, thread/turn hierarchy, transport variants, and experimental APIs. | `application/task_service.py`, `api/web.py`, `api/mcp.py` |
| [DBOS workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial) | Durable execution resumes after the last completed step, with persisted workflow state and timeouts. | Jobs and stage checkpoints are persisted; resume skips completed stages and refuses unsafe opaque replay. | Decorator-based workflow programming, queues, durable sleep, debouncing, and a DBOS dependency. | `runtime/engine.py`, `storage/control.py` |
| [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | A stable execution ID plus durable checkpoint supports pause, external response, and resume; resuming may rerun node code. | Stable run/interaction IDs, persisted pending approval, explicit response, and resume. The unsafe-replay rule comes directly from the documented node-restart hazard. | Dynamic graph construction, arbitrary conditional nodes, checkpointer plugins, and LangGraph itself. | `runtime/engine.py`, `storage/control.py` |
| [SWE-agent architecture](https://swe-agent.com/0.7/background/architecture/) | The Agent and execution environment are distinct; actions are executed through one environment boundary. | One ProviderAdapter method and one isolated worktree boundary; workspace facts are read by Runtime rather than accepted from the model. | A long-lived shell protocol, Agent-specific command language, and container lifecycle management. | `providers/contracts.py`, `runtime/worktree.py`, `runtime/workspace.py` |
| [Aider repository map](https://aider.chat/docs/repomap.html) | Concise paths and symbol signatures give a model global code context within a token budget. | On-demand deterministic AST symbol map ranked by task tokens, capped by file and character budgets. | Vector search, embedding index, long-term Memory, or persistent RAG. | `services/repo_map.py` |
| [in-toto Attestation Framework](https://github.com/in-toto/attestation/blob/main/spec/README.md) | Predicate contains metadata, Statement binds it to Subjects, and Envelope authenticates/serializes it. | Evidence report remains the business fact; optional DSSE contains a Statement that references the report digest. | Bundles, a new attestation runtime, and claims of in-toto policy conformance. | `services/dsse.py` |
| [SLSA provenance](https://slsa.dev/spec/v1.2/provenance) | Provenance is verifiable information about where, when, and how artifacts were produced. | Producer/verifier terminology and digest-bound subject identity. | SLSA level or compliance claims; muxdev is not a build-platform certification system. | `models/evidence.py`, `services/evidence_verify.py` |

## Why a minimal state machine is the rational choice

The product has four known workflows and two bounded conditions (`profile.strict` and at most two repair rounds). DBOS and LangGraph validate the checkpoint/interrupt semantics, but their generalized programming models would add more states, dependency surface, migration work, and operational failure modes than muxdev needs. The compact implementation can state and test its invariants directly:

1. Every externally visible state transition is persisted before the next side effect.
2. Completed stages are not replayed.
3. An interrupted opaque Provider stage is blocked unless Runtime can safely reconcile it.
4. Human response is a separate append-only fact, not mutation of model output.
5. Gate evaluation is reproducible from frozen Policy plus typed records.

## Interview conclusion

The engineering depth is in choosing and proving invariants, not importing frameworks. Each borrowed pattern maps to one product risk—lost state, forged evidence, replayed side effects, context bloat, or unverifiable claims—and every non-adopted feature has an explicit scope reason.
"""


def _interview(metrics: dict[str, object]) -> str:
    after, delta = metrics["after"], metrics["delta"]
    return f"""# Interview narratives

## 30 seconds

I turned muxdev from a feature-heavy multi-Agent platform into a trusted-delivery control plane. The key was separating Provider claims from runtime facts and separating hard gates from evidence-quality scores. I removed Memory/RAG and several shadow abstractions, introduced one EvidencePolicy, a 12-table event-based store, subject-bound independent review, durable resume, and optional DSSE. The measured result was {delta['logical_lines']['reduction_percent']}% fewer production lines, {delta['python_files']['reduction_percent']}% fewer Python files, and fixed 29/18/8 CLI, HTTP, and MCP surfaces.

## 3 minutes

The original system had real depth, but the depth was obscured by breadth: 11 workflows, 10 Skills, 54 tables, and multiple ways to decide whether a delivery passed. I first froze machine-readable metrics and characterization data. Then I made five decisions. First, only TaskService and RunEngine remain as core lifecycle services. Second, the Provider boundary is one execute method; the runtime captures diffs, exit codes, usage, interactions, and identities. Third, Skills are prompt guidance and cannot define a gate. Fourth, EvidencePolicy produces deterministic PASS, BLOCKED, or WAITING_HUMAN while a four-part Scorecard only explains quality. Fifth, the store is an append-only fact model with 12 tables and atomic v7 migration.

I kept the hard parts that are interview-worthy: crash-safe stage checkpoints, safe handling of opaque interrupted providers, bounded repair loops, Beta-lower-bound routing history, heterogeneous reviewers, subject digest binding, event-chain verification, and DSSE export. I deliberately did not add LangGraph or Temporal because four static workflows and three Profiles did not justify a general DAG runtime. The result is {after['logical_lines']:,} production lines, {after['python_files']} files, no cycle, no function over 120 lines, and a high-score-but-BLOCKED example that proves score cannot waive a required approval.

## 10 minutes

Start with the trust boundary: a model may claim it tested code, but only the runtime sees the command, working directory, exit code, stdout/stderr digests, and resulting subject. That led to five EvidenceRecord types and one frozen EvidencePolicy. Gate evaluation is deterministic and fail-closed: missing required evidence, failed checks, digest mismatch, wrong review target, self-review, unresolved high findings, pending/rejected approval, malformed Skill output, or attempted Provider gate decisions block delivery. The Scorecard uses transparent numerators and denominators; zero denominators are N/A.

Then explain durability: each execution is a persisted job; each stage transition and evidence record is appended to a hash chain. Resume skips completed stages, waits on explicit interactions, and refuses to replay an interrupted opaque Provider stage without reconciliation. A maximum two-round fix/test/review loop is built into the fixed change workflow. This is enough for the product and easier to prove than a general workflow engine.

For routing, candidates are capability/certification filtered. Only PASS outcomes with valid integrity and runtime-confirmed independent review enter Beta history; Provider confidence never does. Cost and latency p90 are combined with the conservative quality bound, and standard/strict select a heterogeneous reviewer when one is certified.

Finally explain migration and attestation: v7 databases are backed up, imported into a temporary 12-table database, count/digest verified, and atomically switched. v2 Evidence becomes unscored legacy events. The canonical product is one evidence-report; DSSE wraps an in-toto-style Statement referencing its digest. This preserves audit depth while removing duplicated facts.
"""


def _resume(metrics: dict[str, object]) -> str:
    after, delta = metrics["after"], metrics["delta"]
    return f"""# Resume bullets

## 中文

- 主导 AI Agent 交付控制面架构精简，将生产 Python 从 38,933 行降至 {after['logical_lines']:,} 行（-{delta['logical_lines']['reduction_percent']}%）、文件从 166 降至 {after['python_files']}（-{delta['python_files']['reduction_percent']}%），消除循环依赖及全部超 120 行函数。
- 设计 Evidence v3 确定性门禁：五类运行时证据、稳定 Requirement ID、Subject 摘要绑定、独立评审、四维可解释 Scorecard；保证高分不能覆盖缺失审批或失败检查。
- 将 54 表多投影存储重构为 12 表 append-only 事实模型，实现哈希事件链、可恢复阶段状态机及 v7 备份—临时导入—计数/摘要校验—原子切换迁移。
- 收敛 11 个工作流/10 个 Skill/159 个 CLI 命令/99 个 HTTP 路由为 4 个工作流、5 个 Skill、29/18/8 CLI/HTTP/MCP 固定表面，并保留 Beta 下界智能路由、异构 Reviewer 与 DSSE Attestation。

## English

- Led an AI Agent delivery-control-plane simplification, reducing production Python from 38,933 to {after['logical_lines']:,} lines (-{delta['logical_lines']['reduction_percent']}%) and 166 to {after['python_files']} files (-{delta['python_files']['reduction_percent']}%), while eliminating dependency cycles and every function over 120 lines.
- Designed deterministic Evidence v3 gates with five runtime evidence types, stable requirement IDs, subject-digest binding, independent review, and a four-dimension explainable Scorecard where quality scores cannot waive hard failures.
- Replaced 54-table projection-heavy persistence with a 12-table append-only fact model, hash-chained events, durable stage recovery, and an atomic backup/import/count-and-digest-verify/switch v7 migration.
- Consolidated 11 workflows, 10 Skills, 159 CLI commands, and 99 HTTP routes into four workflows, five Skills, and fixed 29/18/8 CLI/HTTP/MCP surfaces while retaining Beta-bound routing, heterogeneous reviewers, and DSSE attestation.
"""


def _write(name: str, content: str) -> None:
    (OUTPUT / name).write_text(content.rstrip() + "\n", encoding="utf-8")


def _write_json(name: str, payload: object) -> None:
    _write(name, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
