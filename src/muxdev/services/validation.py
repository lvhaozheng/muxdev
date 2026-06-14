"""Executable validation harness for comparing agent strategies."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..models import RunStatus, utc_now
from ..models.validation import (
    ComparisonReport,
    StrategyRun,
    ValidationCase,
    ValidationExperiment,
    ValidationMetric,
    ValidationStrategy,
    ValidationSuite,
)
from ..storage import Blackboard, RunStore
from .evidence import write_evidence_run
from .observability_export import LangSmithPayloadExporter, LangfusePayloadExporter, LocalValidationTraceExporter


DEFAULT_STRATEGIES: list[ValidationStrategy] = ["single_agent", "multi_agent"]


def run_validation_experiment(
    workspace: Path,
    suite_ref: str,
    *,
    strategies: list[str] | None = None,
    provider: str = "mock",
    multi_workflow: str = "software-dev",
    experiment_id: str | None = None,
    export_targets: list[str] | None = None,
) -> ValidationExperiment:
    """Run a validation suite across single-agent and multi-agent strategies."""
    from ..runtime import SupervisorRuntime

    suite = load_validation_suite(workspace, suite_ref)
    selected = _normalize_strategies(strategies or DEFAULT_STRATEGIES)
    experiment_id = experiment_id or f"val_{uuid.uuid4().hex[:10]}"
    experiment_dir = validation_experiment_dir(workspace, experiment_id)
    validation_dir = experiment_dir / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    single_workflow = _write_single_agent_workflow(validation_dir)
    experiment = ValidationExperiment(experiment_id=experiment_id, suite=suite, strategies=selected)

    for case in suite.cases:
        baseline_run_id: str | None = None
        for strategy in selected:
            workflow = str(single_workflow) if strategy == "single_agent" else multi_workflow
            role_providers = _role_providers_for_strategy(strategy, provider)
            run = SupervisorRuntime(workspace).run(
                case.task,
                provider=provider,
                workflow_name=workflow,
                role_providers=role_providers,
                automation={"intent": "validation", "experiment_id": experiment_id, "strategy": strategy, "case_id": case.id},
            )
            strategy_run = StrategyRun(
                task_id=case.id,
                fixture=case.fixture,
                strategy=strategy,
                workflow="single-agent" if strategy == "single_agent" else multi_workflow,
                provider=provider,
                role_providers=role_providers,
                run_id=run.run_id,
                baseline_run_id=baseline_run_id,
                status=str(run.status),
                seed_config={"provider": provider, "suite": suite.name, "case_tags": case.tags},
            )
            experiment.runs.append(strategy_run)
            experiment.metrics.append(collect_validation_metric(workspace, strategy_run))
            if strategy == "single_agent":
                baseline_run_id = run.run_id

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


def collect_validation_metric(workspace: Path, strategy_run: StrategyRun) -> ValidationMetric:
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
    quality_score = _clamp((1.0 if completed else 0.0) * 0.35 + test_pass_rate * 0.25 + _inverse_count(review_blockers, 5) * 0.2 + _inverse_count(high_blockers, 2) * 0.2)
    reliability_score = _clamp((0.0 if status in {"blocked", "aborted", "failed"} else 1.0) * 0.45 + _inverse_count(recover_count, 3) * 0.25 + _inverse_count(len(stage_failure_kinds), 3) * 0.2 + 0.1)
    evidence_score = _clamp(evidence_confidence * 0.75 + _inverse_count(missing_count, 5) * 0.25)
    efficiency_score = _clamp(_inverse_count(total_seconds / 60.0, 30) * 0.4 + _inverse_count(sum(int(row.get("tokens") or 0) for row in usage) / 1000.0, 50) * 0.3 + _inverse_count(sum(float(row.get("cost_usd") or 0.0) for row in usage), 5) * 0.3)
    human_effort_inverse = _inverse_count(human_intervention_count, 3)
    score = _clamp(quality_score * 0.35 + reliability_score * 0.25 + evidence_score * 0.20 + efficiency_score * 0.10 + human_effort_inverse * 0.10)
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
        rollback_success=not any(str(row.get("type") or "") == "rollback_failed" for row in errors),
        stage_failure_kinds=stage_failure_kinds,
        resume_success_rate=1.0 if recover_count == 0 or completed else 0.0,
        plan_test_review_consistency=_plan_test_review_consistency(stages, tests, blockers),
        defect_detection_rate=1.0 if blockers else 0.0,
        independent_review_block_rate=min(1.0, review_blockers / 3.0),
        security_risk_block_rate=1.0 if any("security" in str(row.get("type") or "").lower() for row in blockers) else 0.0,
        quality_score=round(quality_score, 4),
        reliability_score=round(reliability_score, 4),
        evidence_score=round(evidence_score, 4),
        efficiency_score=round(efficiency_score, 4),
        human_effort_inverse=round(human_effort_inverse, 4),
        score=round(score, 4),
    )


def compare_validation_metrics(experiment_id: str, suite: str, metrics: list[ValidationMetric]) -> ComparisonReport:
    by_strategy: dict[str, list[ValidationMetric]] = defaultdict(list)
    for metric in metrics:
        by_strategy[metric.strategy].append(metric)
    strategy_scores = {strategy: round(sum(row.score for row in rows) / max(len(rows), 1), 4) for strategy, rows in by_strategy.items()}
    winner = max(strategy_scores, key=strategy_scores.get) if strategy_scores else "none"
    multi = _aggregate(by_strategy.get("multi_agent", []))
    single = _aggregate(by_strategy.get("single_agent", []))
    advantages: list[str] = []
    tradeoffs: list[str] = []
    if multi.get("score", 0) > single.get("score", 0):
        advantages.append("multi_agent achieved the higher aggregate validation score")
    if multi.get("evidence_confidence", 0) >= single.get("evidence_confidence", 0):
        advantages.append("multi_agent produced equal or stronger evidence confidence")
    if multi.get("high_review_blockers", 0) <= single.get("high_review_blockers", 0):
        advantages.append("multi_agent did not increase high-severity review blockers")
    if multi.get("cost_usd", 0) > single.get("cost_usd", 0):
        tradeoffs.append("multi_agent cost more to run")
    if multi.get("tokens", 0) > single.get("tokens", 0):
        tradeoffs.append("multi_agent used more tokens")
    failure_cases = [
        {"task_id": row.task_id, "strategy": row.strategy, "run_id": row.run_id, "status": row.status, "failures": row.stage_failure_kinds}
        for row in metrics
        if row.blocked or row.stage_failure_kinds
    ]
    recommendation = (
        "Use multi_agent for complex or high-risk tasks; it won this experiment."
        if winner == "multi_agent"
        else "Use single_agent for this suite unless additional review/security evidence is required."
    )
    return ComparisonReport(
        experiment_id=experiment_id,
        suite=suite,
        strategy_scores=strategy_scores,
        winner=winner,
        recommendation=recommendation,
        advantages=advantages or ["No clear multi-agent advantage in this run"],
        tradeoffs=tradeoffs or ["No material cost or token tradeoff detected"],
        failure_cases=failure_cases,
        cost_delta_usd=round(float(multi.get("cost_usd", 0)) - float(single.get("cost_usd", 0)), 6),
        token_delta=int(multi.get("tokens", 0)) - int(single.get("tokens", 0)),
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
        f"- Recommendation: {comparison.recommendation if comparison else '-'}",
        "",
        "## Strategy Scores",
        "| Strategy | Score | Completed | Evidence | Cost | Tokens | Human actions |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    by_strategy: dict[str, list[ValidationMetric]] = defaultdict(list)
    for metric in experiment.metrics:
        by_strategy[metric.strategy].append(metric)
    for strategy, rows in sorted(by_strategy.items()):
        lines.append(
            f"| {strategy} | {_avg(rows, 'score'):.4f} | {_avg(rows, 'completed'):.2f} | {_avg(rows, 'evidence_confidence'):.2f} | {_sum(rows, 'cost_usd'):.4f} | {int(_sum(rows, 'tokens'))} | {int(_sum(rows, 'human_intervention_count'))} |"
        )
    lines.extend(["", "## Task Results", "| Task | Strategy | Run | Status | Score | Missing evidence | Failures |", "| --- | --- | --- | --- | ---: | ---: | --- |"])
    for metric in experiment.metrics:
        lines.append(f"| {metric.task_id} | {metric.strategy} | {metric.run_id} | {metric.status} | {metric.score:.4f} | {metric.missing_evidence_count} | {', '.join(metric.stage_failure_kinds) or '-'} |")
    lines.extend(["", "## Advantages"])
    lines.extend(f"- {item}" for item in (comparison.advantages if comparison else []))
    lines.extend(["", "## Tradeoffs"])
    lines.extend(f"- {item}" for item in (comparison.tradeoffs if comparison else []))
    lines.extend(["", "## Applicability"])
    lines.append("- Prefer single_agent for small, low-risk tasks where speed and cost dominate.")
    lines.append("- Prefer multi_agent when design, testing, review, security, recovery, or audit evidence matter.")
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


def _role_providers_for_strategy(strategy: ValidationStrategy, provider: str) -> dict[str, str]:
    if strategy == "single_agent":
        return {"solo_agent": provider}
    return {"architect": provider, "implementer": provider, "tester": provider, "reviewer": provider, "secure": provider}


def _normalize_strategies(values: list[str]) -> list[ValidationStrategy]:
    result: list[ValidationStrategy] = []
    for value in values:
        strategy = value.strip()
        if strategy not in {"single_agent", "multi_agent"}:
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


def _sum(rows: list[ValidationMetric], attr: str) -> float:
    return sum(float(getattr(row, attr)) for row in rows)
