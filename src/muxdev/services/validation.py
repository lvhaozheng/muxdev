"""Executable validation harness for comparing agent strategies."""

from __future__ import annotations

import json
import subprocess
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..config.runtime import legacy_role, normalize_role
from ..core.platforms import hidden_subprocess_kwargs
from ..core.redaction import redact
from ..models import RunStatus, StageStatus, utc_now
from ..models.validation import (
    ComparisonReport,
    StrategyRun,
    ValidationCase,
    ValidationExperiment,
    ValidationMetric,
    ValidationStrategy,
    ValidationSuite,
)
from ..providers.adapters import get_runtime_provider
from ..storage import Blackboard, RunStore, TraceWriter, sha256_file
from ..workflows import load_workflow
from .evidence import write_evidence_run
from .observability_export import LangSmithPayloadExporter, LangfusePayloadExporter, LocalValidationTraceExporter
from .validation_judge import judge_validation_run


DEFAULT_STRATEGIES: list[ValidationStrategy] = ["direct_cli", "muxdev_single_cli", "muxdev_multi_cli"]


def run_validation_experiment(
    workspace: Path,
    suite_ref: str,
    *,
    strategies: list[str] | None = None,
    provider: str = "mock",
    multi_workflow: str = "software-dev",
    role_providers: dict[str, str] | None = None,
    judge_provider: str | None = None,
    judge_weight: float = 0.2,
    experiment_id: str | None = None,
    export_targets: list[str] | None = None,
) -> ValidationExperiment:
    """Run a validation suite across direct CLI and muxdev orchestration strategies."""
    from ..runtime import SupervisorRuntime

    suite = load_validation_suite(workspace, suite_ref)
    selected = _normalize_strategies(strategies or DEFAULT_STRATEGIES)
    role_providers = _expand_role_providers(role_providers or {})
    experiment_id = experiment_id or f"val_{uuid.uuid4().hex[:10]}"
    experiment_dir = validation_experiment_dir(workspace, experiment_id)
    validation_dir = experiment_dir / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    single_workflow = _write_single_agent_workflow(validation_dir)
    experiment = ValidationExperiment(experiment_id=experiment_id, suite=suite, strategies=selected)

    for case in suite.cases:
        baseline_run_id: str | None = None
        for strategy in selected:
            if strategy == "direct_cli":
                strategy_run = _run_direct_cli_strategy(
                    workspace,
                    case,
                    provider=provider,
                    experiment_id=experiment_id,
                    baseline_run_id=baseline_run_id,
                    seed_config={"provider": provider, "suite": suite.name, "case_tags": case.tags},
                )
            else:
                workflow = _workflow_for_strategy(strategy, single_workflow, multi_workflow)
                selected_role_providers = _role_providers_for_strategy(strategy, provider, workflow, role_providers)
                run = SupervisorRuntime(workspace).run(
                    case.task,
                    provider=provider,
                    workflow_name=workflow,
                    role_providers=selected_role_providers,
                    automation={"intent": "validation", "experiment_id": experiment_id, "strategy": strategy, "case_id": case.id},
                )
                strategy_run = StrategyRun(
                    task_id=case.id,
                    fixture=case.fixture,
                    strategy=strategy,
                    mode="muxdev",
                    workflow="single-agent" if strategy == "single_agent" else multi_workflow,
                    provider=provider,
                    role_providers=selected_role_providers,
                    run_id=run.run_id,
                    baseline_run_id=baseline_run_id,
                    status=str(run.status),
                    output_path=_latest_stage_output_path(workspace, run.run_id),
                    diff_path=str(run.run_dir / "diff.patch") if (run.run_dir / "diff.patch").exists() else None,
                    seed_config={"provider": provider, "suite": suite.name, "case_tags": case.tags},
                )
            judge_payload = None
            if judge_provider:
                judge_payload = judge_validation_run(workspace, strategy_run, judge_provider=judge_provider)
                strategy_run.judge_path = str(judge_payload.get("path") or "") or None
            experiment.runs.append(strategy_run)
            experiment.metrics.append(collect_validation_metric(workspace, strategy_run, judge=judge_payload, judge_weight=judge_weight))
            if strategy == "direct_cli" and baseline_run_id is None:
                baseline_run_id = strategy_run.run_id
            elif baseline_run_id is None:
                baseline_run_id = strategy_run.run_id

    experiment.comparison = compare_validation_metrics(experiment.experiment_id, suite.name, experiment.metrics)
    _write_validation_artifacts(workspace, experiment, export_targets=export_targets or ["local"])
    return load_validation_experiment(workspace, experiment_id)


def load_validation_suite(workspace: Path, suite_ref: str) -> ValidationSuite:
    """Load a suite from a path or validation/suites/<name>.yaml."""
    candidates = [Path(suite_ref)]
    if not Path(suite_ref).suffix:
        candidates.append(workspace / "validation" / "suites" / f"{suite_ref}.yaml")
        candidates.append(workspace / "validation" / "suites" / f"{suite_ref}.yml")
    candidates.append(workspace / suite_ref)
    for path in candidates:
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            suite = ValidationSuite.model_validate(data)
            if not suite.cases:
                raise ValueError(f"validation suite has no cases: {path}")
            return suite
    raise FileNotFoundError(f"validation suite not found: {suite_ref}")


def collect_validation_metric(
    workspace: Path,
    strategy_run: StrategyRun,
    *,
    judge: dict[str, Any] | None = None,
    judge_weight: float = 0.2,
) -> ValidationMetric:
    run_dir = RunStore(workspace).find_run_dir(strategy_run.run_id)
    board = Blackboard(run_dir)
    try:
        run = board.get_run(strategy_run.run_id)
        stages = board.table_rows("stages", run_id=strategy_run.run_id)
        usage = board.table_rows("usage_records", run_id=strategy_run.run_id)
        attempts = board.table_rows("provider_attempts", run_id=strategy_run.run_id)
        actions = board.table_rows("provider_actions", run_id=strategy_run.run_id)
        approvals = board.table_rows("approvals", run_id=strategy_run.run_id)
        tests = board.table_rows("test_results", run_id=strategy_run.run_id)
        blockers = board.table_rows("review_blockers", run_id=strategy_run.run_id)
        errors = board.table_rows("error_details", run_id=strategy_run.run_id)
        evaluations = board.table_rows("evidence_evaluations", run_id=strategy_run.run_id)
    finally:
        board.close()

    status = str(run.get("status") or strategy_run.status)
    completed = status == str(RunStatus.COMPLETED)
    test_pass_rate = _test_pass_rate(tests)
    high_blockers = sum(1 for row in blockers if str(row.get("severity") or "").lower() == "high")
    evidence = evaluations[-1] if evaluations else {}
    evidence_confidence = float(evidence.get("confidence") or 0.0)
    missing_count = len(evidence.get("missing_evidence") or [])
    stage_seconds = {str(row.get("stage_id")): _duration(row.get("started_at"), row.get("completed_at")) for row in stages}
    total_seconds = _duration(run.get("created_at"), run.get("updated_at")) or sum(stage_seconds.values())
    retry_count = sum(max(0, int(row.get("attempt") or 1) - 1) for row in attempts)
    provider_action_count = len(actions)
    human_intervention_count = len(actions) + len([row for row in approvals if str(row.get("status") or "") == "approved"])
    recover_count = sum(1 for row in errors if "recover" in str(row.get("type") or "").lower())
    stage_failure_kinds = sorted({str(row.get("type") or "") for row in errors if row.get("type")})
    review_blockers = len(blockers)
    output_nonempty = bool(strategy_run.output_path and Path(strategy_run.output_path).exists() and Path(strategy_run.output_path).stat().st_size > 0)
    diff_nonempty = bool(strategy_run.diff_path and Path(strategy_run.diff_path).exists() and Path(strategy_run.diff_path).stat().st_size > 0)
    quality_score = _clamp((1.0 if completed else 0.0) * 0.35 + test_pass_rate * 0.25 + _inverse_count(review_blockers, 5) * 0.2 + _inverse_count(high_blockers, 2) * 0.2)
    reliability_score = _clamp((0.0 if status in {"blocked", "aborted", "failed"} else 1.0) * 0.45 + _inverse_count(recover_count, 3) * 0.25 + _inverse_count(len(stage_failure_kinds), 3) * 0.2 + 0.1)
    evidence_score = _clamp(evidence_confidence * 0.75 + _inverse_count(missing_count, 5) * 0.25)
    efficiency_score = _clamp(_inverse_count(total_seconds / 60.0, 30) * 0.4 + _inverse_count(sum(int(row.get("tokens") or 0) for row in usage) / 1000.0, 50) * 0.3 + _inverse_count(sum(float(row.get("cost_usd") or 0.0) for row in usage), 5) * 0.3)
    human_effort_inverse = _inverse_count(human_intervention_count, 3)
    rollback_success = not any(str(row.get("type") or "") == "rollback_failed" for row in errors)
    plan_test_review_consistency = _plan_test_review_consistency(stages, tests, blockers)
    task_completion_score = _clamp((1.0 if completed else 0.0) * 0.75 + (0.15 if output_nonempty else 0.0) + (0.10 if diff_nonempty else 0.0))
    answer_quality_score = _clamp(quality_score * 0.7 + (0.3 if output_nonempty else 0.0))
    process_floor = 0.25 if strategy_run.strategy == "direct_cli" else 0.55
    process_score = _clamp(process_floor + plan_test_review_consistency * 0.25 + evidence_score * 0.2)
    safety_score = _clamp(_inverse_count(high_blockers + len(stage_failure_kinds), 4) * 0.55 + (1.0 if rollback_success else 0.0) * 0.25 + _inverse_count(provider_action_count, 3) * 0.20)
    deterministic_score = _clamp(
        task_completion_score * 0.25
        + answer_quality_score * 0.20
        + process_score * 0.20
        + reliability_score * 0.15
        + safety_score * 0.10
        + efficiency_score * 0.10
    )
    normalized_judge_weight = _clamp(judge_weight) if judge else 0.0
    judge_score = _normalized_judge_score(judge.get("score")) if judge else None
    if judge_score is not None:
        score = _clamp(deterministic_score * (1.0 - normalized_judge_weight) + judge_score * normalized_judge_weight)
    else:
        score = deterministic_score
    judge_reasons = [str(item) for item in (judge.get("reasons") if judge else []) or []]
    judge_pass = bool(judge.get("pass")) if judge and judge.get("pass") is not None else None
    return ValidationMetric(
        task_id=strategy_run.task_id,
        strategy=strategy_run.strategy,
        run_id=strategy_run.run_id,
        status=status,
        completed=completed,
        test_pass_rate=test_pass_rate,
        review_blockers=review_blockers,
        high_review_blockers=high_blockers,
        evidence_confidence=evidence_confidence,
        missing_evidence_count=missing_count,
        total_seconds=round(total_seconds, 4),
        stage_seconds={key: round(value, 4) for key, value in stage_seconds.items()},
        tokens=sum(int(row.get("tokens") or 0) for row in usage),
        cost_usd=round(sum(float(row.get("cost_usd") or 0.0) for row in usage), 6),
        retry_count=retry_count,
        provider_action_count=provider_action_count,
        human_intervention_count=human_intervention_count,
        recover_count=recover_count,
        blocked=status in {"blocked", "aborted", "failed"},
        rollback_success=rollback_success,
        stage_failure_kinds=stage_failure_kinds,
        resume_success_rate=1.0 if recover_count == 0 or completed else 0.0,
        plan_test_review_consistency=plan_test_review_consistency,
        defect_detection_rate=1.0 if blockers else 0.0,
        independent_review_block_rate=min(1.0, review_blockers / 3.0),
        security_risk_block_rate=1.0 if any("security" in str(row.get("type") or "").lower() for row in blockers) else 0.0,
        quality_score=round(quality_score, 4),
        reliability_score=round(reliability_score, 4),
        evidence_score=round(evidence_score, 4),
        efficiency_score=round(efficiency_score, 4),
        human_effort_inverse=round(human_effort_inverse, 4),
        task_completion_score=round(task_completion_score, 4),
        answer_quality_score=round(answer_quality_score, 4),
        process_score=round(process_score, 4),
        safety_score=round(safety_score, 4),
        judge_score=round(judge_score, 4) if judge_score is not None else None,
        judge_pass=judge_pass,
        judge_reasons=judge_reasons,
        score=round(score, 4),
    )


def compare_validation_metrics(experiment_id: str, suite: str, metrics: list[ValidationMetric]) -> ComparisonReport:
    by_strategy: dict[str, list[ValidationMetric]] = defaultdict(list)
    for metric in metrics:
        by_strategy[metric.strategy].append(metric)
    strategy_scores = {strategy: round(sum(row.score for row in rows) / max(len(rows), 1), 4) for strategy, rows in by_strategy.items()}
    winner = max(strategy_scores, key=strategy_scores.get) if strategy_scores else "none"
    baseline_strategy = "direct_cli" if by_strategy.get("direct_cli") else (next(iter(strategy_scores), "none"))
    direct = _aggregate(by_strategy.get("direct_cli", []))
    single_cli = _aggregate(by_strategy.get("muxdev_single_cli", []) + by_strategy.get("single_agent", []))
    multi_cli = _aggregate(by_strategy.get("muxdev_multi_cli", []) + by_strategy.get("multi_agent", []))
    best_muxdev = _aggregate([row for row in metrics if str(row.strategy).startswith("muxdev_") or row.strategy in {"single_agent", "multi_agent"}])
    advantages: list[str] = []
    tradeoffs: list[str] = []
    if best_muxdev.get("score", 0) > direct.get("score", 0):
        advantages.append("muxdev orchestration achieved a higher aggregate validation score than direct_cli")
    if best_muxdev.get("evidence_confidence", 0) >= direct.get("evidence_confidence", 0):
        advantages.append("muxdev orchestration produced equal or stronger evidence confidence")
    if multi_cli and single_cli and multi_cli.get("score", 0) > single_cli.get("score", 0):
        advantages.append("muxdev_multi_cli outscored muxdev_single_cli")
    if best_muxdev.get("cost_usd", 0) > direct.get("cost_usd", 0):
        tradeoffs.append("muxdev orchestration cost more than direct_cli")
    if best_muxdev.get("tokens", 0) > direct.get("tokens", 0):
        tradeoffs.append("muxdev orchestration used more tokens than direct_cli")
    failure_cases = [
        {"task_id": row.task_id, "strategy": row.strategy, "run_id": row.run_id, "status": row.status, "failures": row.stage_failure_kinds}
        for row in metrics
        if row.blocked or row.stage_failure_kinds
    ]
    recommendation = (
        "Use muxdev orchestration for this suite; it beat the direct CLI baseline."
        if winner != "direct_cli" and best_muxdev.get("score", 0) > direct.get("score", 0)
        else "Use direct_cli for this suite unless process evidence, review, recovery, or governance are required."
    )
    baseline_score = float(strategy_scores.get(baseline_strategy, 0.0))
    muxdev_delta = {
        strategy: round(score - baseline_score, 4)
        for strategy, score in strategy_scores.items()
        if strategy != baseline_strategy
    }
    judged = [row for row in metrics if row.judge_score is not None]
    return ComparisonReport(
        experiment_id=experiment_id,
        suite=suite,
        strategy_scores=strategy_scores,
        winner=winner,
        recommendation=recommendation,
        advantages=advantages or ["No clear multi-agent advantage in this run"],
        tradeoffs=tradeoffs or ["No material cost or token tradeoff detected"],
        failure_cases=failure_cases,
        cost_delta_usd=round(float(best_muxdev.get("cost_usd", 0)) - float(direct.get("cost_usd", 0)), 6),
        token_delta=int(best_muxdev.get("tokens", 0)) - int(direct.get("tokens", 0)),
        baseline_strategy=baseline_strategy,
        muxdev_delta=muxdev_delta,
        judge_summary={
            "judged_runs": len(judged),
            "average_judge_score": round(sum(float(row.judge_score or 0.0) for row in judged) / max(len(judged), 1), 4) if judged else None,
            "failed_judgments": len([row for row in judged if row.judge_pass is False]),
        },
    )


def validation_experiment_dir(workspace: Path, experiment_id: str) -> Path:
    return workspace / ".muxdev" / "runs" / experiment_id


def list_validation_experiments(workspace: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((workspace / ".muxdev" / "runs").glob("*/validation/experiment.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "experiment_id": data.get("experiment_id"),
                "suite": (data.get("suite") or {}).get("name"),
                "strategies": data.get("strategies", []),
                "winner": ((data.get("comparison") or {}).get("winner")),
                "report": (data.get("artifacts") or {}).get("report"),
                "updated_at": data.get("updated_at"),
            }
        )
    return sorted(rows, key=lambda row: str(row.get("updated_at") or ""), reverse=True)


def load_validation_experiment(workspace: Path, experiment_id: str) -> ValidationExperiment:
    path = validation_experiment_dir(workspace, experiment_id) / "validation" / "experiment.json"
    return ValidationExperiment.model_validate_json(path.read_text(encoding="utf-8"))


def render_validation_report(experiment: ValidationExperiment) -> str:
    comparison = experiment.comparison
    lines = [
        f"# muxdev validation report: {experiment.experiment_id}",
        "",
        f"- Suite: {experiment.suite.name}",
        f"- Strategies: {', '.join(experiment.strategies)}",
        f"- Winner: {comparison.winner if comparison else '-'}",
        f"- Baseline: {comparison.baseline_strategy if comparison else '-'}",
        f"- Recommendation: {comparison.recommendation if comparison else '-'}",
        "",
        "## Strategy Scores",
        "| Strategy | Score | Completion | Quality | Process | Safety | Evidence | Cost | Tokens | Judge |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    by_strategy: dict[str, list[ValidationMetric]] = defaultdict(list)
    for metric in experiment.metrics:
        by_strategy[metric.strategy].append(metric)
    for strategy, rows in sorted(by_strategy.items()):
        lines.append(
            f"| {strategy} | {_avg(rows, 'score'):.4f} | {_avg(rows, 'task_completion_score'):.2f} | {_avg(rows, 'answer_quality_score'):.2f} | {_avg(rows, 'process_score'):.2f} | {_avg(rows, 'safety_score'):.2f} | {_avg(rows, 'evidence_confidence'):.2f} | {_sum(rows, 'cost_usd'):.4f} | {int(_sum(rows, 'tokens'))} | {_avg_optional(rows, 'judge_score')} |"
        )
    lines.extend(["", "## Task Results", "| Task | Strategy | Run | Status | Score | Missing evidence | Failures |", "| --- | --- | --- | --- | ---: | ---: | --- |"])
    for metric in experiment.metrics:
        lines.append(f"| {metric.task_id} | {metric.strategy} | {metric.run_id} | {metric.status} | {metric.score:.4f} | {metric.missing_evidence_count} | {', '.join(metric.stage_failure_kinds) or '-'} |")
    lines.extend(["", "## Advantages"])
    lines.extend(f"- {item}" for item in (comparison.advantages if comparison else []))
    lines.extend(["", "## Tradeoffs"])
    lines.extend(f"- {item}" for item in (comparison.tradeoffs if comparison else []))
    direct_runs = [run for run in experiment.runs if run.strategy == "direct_cli"]
    lines.extend(["", "## Direct CLI Baseline"])
    if direct_runs:
        for run in direct_runs:
            lines.append(f"- {run.task_id}: run={run.run_id}, output={run.output_path or '-'}, diff={run.diff_path or '-'}")
    else:
        lines.append("- direct_cli was not included in this experiment.")
    lines.extend(["", "## muxdev Value Add"])
    if comparison:
        for strategy, delta in comparison.muxdev_delta.items():
            lines.append(f"- {strategy}: score delta vs {comparison.baseline_strategy} = {delta:+.4f}")
    lines.append(f"- Cost delta vs baseline: {comparison.cost_delta_usd if comparison else 0.0:+.6f} USD")
    lines.append(f"- Token delta vs baseline: {comparison.token_delta if comparison else 0:+d}")
    lines.extend(["", "## Judge Review"])
    judge_summary = comparison.judge_summary if comparison else {}
    if judge_summary.get("judged_runs"):
        lines.append(f"- Judged runs: {judge_summary.get('judged_runs')}")
        lines.append(f"- Average judge score: {judge_summary.get('average_judge_score')}")
        lines.append(f"- Failed judgments: {judge_summary.get('failed_judgments')}")
        for metric in experiment.metrics:
            if metric.judge_score is not None:
                lines.append(f"- {metric.task_id}/{metric.strategy}: {metric.judge_score:.2f} ({'; '.join(metric.judge_reasons) or 'no reasons'})")
    else:
        lines.append("- LLM-as-a-Judge was not enabled for this experiment.")
    lines.extend(["", "## Applicability"])
    lines.append("- Prefer direct_cli for small, low-risk tasks where speed and cost dominate.")
    lines.append("- Prefer muxdev_single_cli when evidence, review, recovery, or governance matter but one CLI should do the work.")
    lines.append("- Prefer muxdev_multi_cli when roles benefit from different provider strengths.")
    return "\n".join(lines) + "\n"


def _write_validation_artifacts(workspace: Path, experiment: ValidationExperiment, *, export_targets: list[str]) -> None:
    root = validation_experiment_dir(workspace, experiment.experiment_id)
    validation_dir = root / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = workspace / "reports" / "validation"
    reports_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = validation_dir / "metrics.json"
    comparison_path = validation_dir / "comparison.json"
    report_path = reports_dir / f"{experiment.experiment_id}.md"
    metrics_path.write_text(json.dumps([metric.model_dump() for metric in experiment.metrics], ensure_ascii=False, indent=2), encoding="utf-8")
    if experiment.comparison:
        comparison_path.write_text(experiment.comparison.model_dump_json(indent=2), encoding="utf-8")
    report_path.write_text(render_validation_report(experiment), encoding="utf-8")
    experiment.artifacts.update({"metrics": str(metrics_path), "comparison": str(comparison_path), "report": str(report_path)})
    experiment.updated_at = utc_now()
    experiment_path = validation_dir / "experiment.json"
    experiment_path.write_text(experiment.model_dump_json(indent=2), encoding="utf-8")
    _write_exports(experiment, validation_dir, export_targets)
    experiment_path.write_text(experiment.model_dump_json(indent=2), encoding="utf-8")
    _record_validation_artifacts(workspace, experiment)


def _write_exports(experiment: ValidationExperiment, validation_dir: Path, export_targets: list[str]) -> None:
    exporters = {
        "local": LocalValidationTraceExporter(),
        "langfuse": LangfusePayloadExporter(),
        "langsmith": LangSmithPayloadExporter(),
    }
    for target in export_targets:
        exporter = exporters.get(target)
        if exporter is None:
            continue
        result = exporter.export_experiment(experiment, validation_dir / "exports" / target)
        experiment.artifacts[f"{target}_trace"] = str(result.path)


def _record_validation_artifacts(workspace: Path, experiment: ValidationExperiment) -> None:
    for run in experiment.runs:
        run_dir = RunStore(workspace).find_run_dir(run.run_id)
        board = Blackboard(run_dir)
        try:
            for name, path in experiment.artifacts.items():
                board.add_artifact(run.run_id, None, Path(path).name, Path(path), f"validation_{name}")
            write_evidence_run(run_dir, run.run_id, board)
        finally:
            board.close()


def _run_direct_cli_strategy(
    workspace: Path,
    case: ValidationCase,
    *,
    provider: str,
    experiment_id: str,
    baseline_run_id: str | None,
    seed_config: dict[str, Any],
) -> StrategyRun:
    from ..runtime.stage_attempt import provider_attempt_status, provider_failure_kind, run_provider_stage
    from ..runtime.worktree import WorktreeManager

    run_id = f"direct_{uuid.uuid4().hex[:10]}"
    store = RunStore(workspace)
    run_dir = store.create_run_dir(run_id)
    worktree = WorktreeManager(workspace).prepare(run_id, run_dir)
    provider_impl = get_runtime_provider(provider)
    board = Blackboard(run_dir)
    trace = TraceWriter(run_dir, run_id)
    task_path = run_dir / "task.md"
    workflow_path = run_dir / "workflow.yaml"
    output_path = run_dir / "direct_output.md"
    diff_path = run_dir / "diff.patch"
    try:
        task_path.write_text(redact(case.task) + "\n", encoding="utf-8")
        workflow_path.write_text(
            yaml.safe_dump({"name": "direct-cli", "stages": [{"id": "direct", "role": "direct_cli"}]}, sort_keys=False),
            encoding="utf-8",
        )
        board.create_run(run_id=run_id, task=redact(case.task), workflow="direct-cli", provider=provider, workspace=workspace, worktree=worktree.path)
        board.upsert_agent(run_id, "direct_cli", provider)
        board.add_artifact(run_id, None, "task.md", task_path, "task")
        board.add_artifact(run_id, None, "workflow.yaml", workflow_path, "workflow")
        board.upsert_stage(run_id, "direct", role="direct_cli", status=StageStatus.RUNNING)
        board.start_provider_attempt(run_id, "direct", provider=provider, role="direct_cli", attempt=1)
        trace.write("run_started", provider=provider, worktree=str(worktree.path), strategy=worktree.strategy, intent="validation", experiment_id=experiment_id)
        trace.write("stage_started", stage="direct", role="direct_cli", type="agent")
        output = run_provider_stage(
            provider_impl,
            stage_id="direct",
            task=_direct_cli_prompt(case.task),
            worktree=worktree.path,
            skills=[],
            session_dir=run_dir / "provider_sessions",
        )
        output_path.write_text(redact(output.content), encoding="utf-8")
        board.add_usage(run_id, provider, output.tokens, output.cost_usd)
        board.add_artifact(run_id, "direct", output_path.name, output_path, "direct_output")
        status = provider_attempt_status(output)
        failure_kind = provider_failure_kind(output)
        board.complete_provider_attempt(
            run_id,
            "direct",
            provider=provider,
            attempt=1,
            status=status,
            failure_kind=failure_kind,
            returncode=output.returncode,
            summary=output.summary,
            artifact_path=str(output_path),
        )
        diff_text = _worktree_diff_text(worktree.path)
        diff_path.write_text(diff_text, encoding="utf-8")
        board.add_artifact(run_id, None, diff_path.name, diff_path, "diff")
        if output.returncode == 0:
            board.upsert_stage(run_id, "direct", role="direct_cli", status=StageStatus.COMPLETED, output_path=str(output_path), summary=output.summary)
            board.set_run_status(run_id, RunStatus.COMPLETED)
            trace.write("stage_completed", stage="direct", output=str(output_path))
            trace.write("run_completed", diff=str(diff_path), diff_hash=sha256_file(diff_path))
        else:
            board.upsert_stage(run_id, "direct", role="direct_cli", status=StageStatus.FAILED, output_path=str(output_path), summary=output.summary)
            board.add_error(run_id, "direct", failure_kind or "provider_exit", output.summary)
            board.set_run_status(run_id, RunStatus.BLOCKED)
            trace.write("stage_failed", stage="direct", returncode=output.returncode, failure_kind=failure_kind)
        write_evidence_run(run_dir, run_id, board)
        final_status = str(board.get_run(run_id).get("status") or "")
    finally:
        board.close()
    return StrategyRun(
        task_id=case.id,
        fixture=case.fixture,
        strategy="direct_cli",
        mode="direct_cli",
        workflow="direct-cli",
        provider=provider,
        role_providers={},
        run_id=run_id,
        baseline_run_id=baseline_run_id,
        status=final_status,
        output_path=str(output_path),
        diff_path=str(diff_path),
        seed_config=seed_config,
    )


def _direct_cli_prompt(task: str) -> str:
    return (
        "Run this task directly as a single CLI assistant. "
        "Return the best final answer or implementation you can produce in one pass. "
        "If you change files, stay inside the current workspace.\n\n"
        f"Task: {task}"
    )


def _write_single_agent_workflow(validation_dir: Path) -> Path:
    path = validation_dir / "single_agent.workflow.yaml"
    data = {
        "name": "single-agent",
        "max_parallel": 1,
        "stages": [
            {"id": "plan", "role": "solo_agent", "deps": [], "read_only": True},
            {"id": "code", "role": "solo_agent", "deps": ["plan"], "allow_write": True, "checkpoint": True},
            {"id": "test", "role": "solo_agent", "deps": ["code"], "allow_shell": True, "output_schema": "TestResult"},
            {"id": "review", "role": "solo_agent", "deps": ["test"], "read_only": True, "output_schema": "ReviewResult"},
        ],
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _workflow_for_strategy(strategy: ValidationStrategy, single_workflow: Path, multi_workflow: str) -> str:
    if strategy == "single_agent":
        return str(single_workflow)
    return multi_workflow


def _role_providers_for_strategy(
    strategy: ValidationStrategy,
    provider: str,
    workflow: str,
    explicit_role_providers: dict[str, str] | None = None,
) -> dict[str, str]:
    explicit_role_providers = explicit_role_providers or {}
    if strategy == "direct_cli":
        return {}
    if strategy == "muxdev_multi_cli":
        return dict(explicit_role_providers)
    if strategy == "single_agent":
        return {"solo_agent": provider}
    roles = _workflow_roles(workflow)
    if not roles:
        roles = {"architect", "implementer", "tester", "reviewer", "secure"}
    return {role: provider for role in roles}


def _expand_role_providers(role_providers: dict[str, str]) -> dict[str, str]:
    expanded: dict[str, str] = {}
    for role, provider in role_providers.items():
        normalized = normalize_role(role)
        expanded[normalized] = provider
        expanded[legacy_role(normalized)] = provider
    return expanded


def _workflow_roles(workflow: str) -> set[str]:
    try:
        definition = load_workflow(workflow)
    except Exception:
        return set()
    return {str(stage.role) for stage in definition.stages if stage.role}


def _normalize_strategies(values: list[str]) -> list[ValidationStrategy]:
    result: list[ValidationStrategy] = []
    for value in values:
        strategy = value.strip()
        if strategy not in {"direct_cli", "muxdev_single_cli", "muxdev_multi_cli", "single_agent", "multi_agent"}:
            raise ValueError(f"unknown validation strategy: {strategy}")
        result.append(strategy)  # type: ignore[arg-type]
    return result or DEFAULT_STRATEGIES


def _test_pass_rate(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if int(row.get("passed") or 0)) / len(rows)


def _plan_test_review_consistency(stages: list[dict[str, Any]], tests: list[dict[str, Any]], blockers: list[dict[str, Any]]) -> float:
    stage_ids = {str(row.get("stage_id") or "") for row in stages if str(row.get("status") or "") == "completed"}
    plan_ok = bool(stage_ids & {"plan", "design", "problem_statement"})
    test_ok = bool(tests) and _test_pass_rate(tests) > 0
    review_ok = "review" in stage_ids or bool(blockers)
    return (float(plan_ok) + float(test_ok) + float(review_ok)) / 3.0


def _duration(start: object, end: object) -> float:
    if not start or not end:
        return 0.0
    try:
        started = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        ended = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (ended - started).total_seconds())


def _inverse_count(value: float | int, max_value: float | int) -> float:
    if max_value <= 0:
        return 1.0
    return _clamp(1.0 - (float(value) / float(max_value)))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalized_judge_score(value: object) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score > 1.0:
        score = score / 100.0
    return _clamp(score)


def _latest_stage_output_path(workspace: Path, run_id: str) -> str | None:
    run_dir = RunStore(workspace).find_run_dir(run_id)
    board = Blackboard(run_dir)
    try:
        outputs = [row for row in board.table_rows("artifacts", run_id=run_id) if row.get("kind") == "stage_output"]
    finally:
        board.close()
    if not outputs:
        return None
    return str(outputs[-1].get("path") or "") or None


def _worktree_diff_text(worktree: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--", "."],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    diff = result.stdout or ""
    diff += "".join(_untracked_file_diff(worktree, rel) for rel in _iter_untracked_files(worktree))
    return redact(diff)


def _iter_untracked_files(worktree: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode == 0:
        return [line for line in (result.stdout or "").splitlines() if line and not line.startswith(".muxdev/provider_sessions/")]
    files: list[str] = []
    for path in worktree.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel_path = path.relative_to(worktree).as_posix()
        if rel_path.startswith(".muxdev/provider_sessions/"):
            continue
        files.append(rel_path)
    return files


def _untracked_file_diff(worktree: Path, rel_path: str) -> str:
    path = worktree / rel_path
    if not path.is_file():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    header = [
        f"diff --git a/{rel_path} b/{rel_path}",
        "new file mode 100644",
        "index 0000000..0000000",
        "--- /dev/null",
        f"+++ b/{rel_path}",
        f"@@ -0,0 +1,{len(lines)} @@",
    ]
    return "\n".join(header + [f"+{line}" for line in lines]) + "\n"


def _aggregate(rows: list[ValidationMetric]) -> dict[str, float]:
    if not rows:
        return {}
    return {
        "score": _avg(rows, "score"),
        "evidence_confidence": _avg(rows, "evidence_confidence"),
        "high_review_blockers": _sum(rows, "high_review_blockers"),
        "cost_usd": _sum(rows, "cost_usd"),
        "tokens": _sum(rows, "tokens"),
    }


def _avg(rows: list[ValidationMetric], attr: str) -> float:
    return _sum(rows, attr) / max(len(rows), 1)


def _avg_optional(rows: list[ValidationMetric], attr: str) -> str:
    values = [float(value) for row in rows if (value := getattr(row, attr)) is not None]
    if not values:
        return "-"
    return f"{sum(values) / len(values):.2f}"


def _sum(rows: list[ValidationMetric], attr: str) -> float:
    return sum(float(getattr(row, attr)) for row in rows)
