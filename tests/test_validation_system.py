from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.cli import app
from muxdev.api.web import create_app, render_live_dashboard_html
from muxdev.models.validation import ComparisonReport, StrategyRun, ValidationExperiment, ValidationMetric, ValidationSuite
from muxdev.services.observability_export import LangSmithPayloadExporter, LangfusePayloadExporter, LocalValidationTraceExporter
from muxdev.services.validation import compare_validation_metrics, run_validation_experiment


runner = CliRunner()


def _workspace(name: str) -> Path:
    path = Path(".test_workspaces") / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_validation_comparison_prefers_higher_multi_agent_score() -> None:
    single = _metric("task", "muxdev_single_cli", "run_single", score=0.55, evidence=0.6, cost=0.01, tokens=100)
    multi = _metric("task", "muxdev_multi_cli", "run_multi", score=0.82, evidence=0.9, cost=0.03, tokens=250)

    report = compare_validation_metrics("val_test", "suite", [single, multi])

    assert report.winner == "muxdev_multi_cli"
    assert report.strategy_scores["muxdev_multi_cli"] > report.strategy_scores["muxdev_single_cli"]
    assert report.baseline_strategy == "muxdev_single_cli"
    assert report.muxdev_delta["muxdev_multi_cli"] > 0


def test_validation_exporters_write_stable_payloads() -> None:
    workspace = _workspace("validation-export")
    try:
        experiment = ValidationExperiment(
            experiment_id="val_export",
            suite=ValidationSuite(name="suite", cases=[]),
            strategies=["direct_cli", "muxdev_single_cli"],
            runs=[
                StrategyRun(task_id="task", strategy="direct_cli", mode="direct_cli", workflow="direct-cli", provider="mock", run_id="run_direct", status="completed"),
                StrategyRun(task_id="task", strategy="muxdev_single_cli", mode="muxdev", workflow="software-dev", provider="mock", run_id="run_single", status="completed"),
            ],
            metrics=[
                _metric("task", "direct_cli", "run_direct", score=0.7, evidence=0.8, cost=0.01, tokens=100),
                _metric("task", "muxdev_single_cli", "run_single", score=0.8, evidence=0.9, cost=0.02, tokens=150),
            ],
            comparison=ComparisonReport(
                experiment_id="val_export",
                suite="suite",
                strategy_scores={"direct_cli": 0.7, "muxdev_single_cli": 0.8},
                winner="muxdev_single_cli",
                recommendation="Use muxdev",
                muxdev_delta={"muxdev_single_cli": 0.1},
            ),
        )
        local = LocalValidationTraceExporter().export_experiment(experiment, workspace / "local")
        langfuse = LangfusePayloadExporter().export_experiment(experiment, workspace / "langfuse")
        langsmith = LangSmithPayloadExporter().export_experiment(experiment, workspace / "langsmith")

        assert local.path.name == "otel_spans.json"
        assert local.spans >= 3
        assert json.loads(langfuse.path.read_text(encoding="utf-8"))["traces"][0]["name"] == "validation.experiment"
        langsmith_payload = json.loads(langsmith.path.read_text(encoding="utf-8"))
        assert langsmith_payload["project_name"] == "muxdev-validation"
        metric_run = next(row for row in langsmith_payload["runs"] if row["name"] == "validation.metrics")
        assert metric_run["extra"]["muxdev.task_completion_score"] == 0.7
        muxdev_metric_run = next(row for row in langsmith_payload["runs"] if row["name"] == "validation.metrics" and row["extra"]["muxdev.strategy"] == "muxdev_single_cli")
        assert muxdev_metric_run["extra"]["muxdev.baseline_delta"] == 0.1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_validation_harness_runs_mock_suite_and_publishes_report() -> None:
    workspace = _workspace("validation-harness")
    try:
        suite = workspace / "validation" / "suites" / "snake.yaml"
        suite.parent.mkdir(parents=True, exist_ok=True)
        suite.write_text(
            "name: snake\n"
            "description: snake game validation\n"
            "cases:\n"
            "  - id: snake_design\n"
            "    task: design a snake game plan\n"
            "    tags: [game, design]\n",
            encoding="utf-8",
        )

        experiment = run_validation_experiment(workspace, "snake", provider="mock", experiment_id="val_snake")

        assert experiment.experiment_id == "val_snake"
        assert {run.strategy for run in experiment.runs} == {"direct_cli", "muxdev_single_cli", "muxdev_multi_cli"}
        assert len(experiment.metrics) == 3
        direct = next(run for run in experiment.runs if run.strategy == "direct_cli")
        assert direct.output_path and Path(direct.output_path).exists()
        assert direct.diff_path and Path(direct.diff_path).exists()
        assert all(metric.judge_score is None and metric.judge_pass is None and not metric.judge_reasons for metric in experiment.metrics)
        assert experiment.comparison is not None
        report = workspace / "reports" / "validation" / "val_snake.md"
        assert report.exists()
        assert "Strategy Scores" in report.read_text(encoding="utf-8")
        assert (workspace / ".muxdev" / "runs" / "val_snake" / "validation" / "experiment.json").exists()
        assert (workspace / ".muxdev" / "runs" / "val_snake" / "validation" / "exports" / "local" / "otel_spans.json").exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_validation_legacy_strategy_names_map_to_muxdev_single_cli() -> None:
    workspace = _workspace("validation-legacy")
    try:
        suite = workspace / "validation" / "suites" / "legacy.yaml"
        suite.parent.mkdir(parents=True, exist_ok=True)
        suite.write_text(
            "name: legacy\n"
            "cases:\n"
            "  - id: legacy_case\n"
            "    task: validate old strategy aliases\n",
            encoding="utf-8",
        )

        experiment = run_validation_experiment(
            workspace,
            "legacy",
            provider="mock",
            strategies=["single_agent", "multi_agent"],
            experiment_id="val_legacy",
        )

        assert experiment.strategies == ["muxdev_single_cli"]
        assert [run.strategy for run in experiment.runs] == ["muxdev_single_cli"]
        assert experiment.runs[0].workflow == "software-dev"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_validation_harness_applies_multi_cli_role_overrides_and_optional_judge() -> None:
    workspace = _workspace("validation-judge")
    try:
        suite = workspace / "validation" / "suites" / "judge.yaml"
        suite.parent.mkdir(parents=True, exist_ok=True)
        suite.write_text(
            "name: judge\n"
            "cases:\n"
            "  - id: judge_case\n"
            "    task: implement a tiny judged change\n",
            encoding="utf-8",
        )

        experiment = run_validation_experiment(
            workspace,
            "judge",
            provider="mock",
            strategies=["direct_cli", "muxdev_multi_cli"],
            role_providers={"code": "mock", "test": "mock"},
            judge_provider="mock",
            experiment_id="val_judge",
        )

        multi = next(run for run in experiment.runs if run.strategy == "muxdev_multi_cli")
        assert multi.role_providers["code"] == "mock"
        assert multi.role_providers["implementer"] == "mock"
        assert multi.role_providers["test"] == "mock"
        assert multi.role_providers["tester"] == "mock"
        assert multi.judge_path and Path(multi.judge_path).exists()
        judged = [metric for metric in experiment.metrics if metric.judge_score is not None]
        assert len(judged) == 2
        assert all(metric.judge_pass is True for metric in judged)
        assert experiment.comparison is not None
        assert experiment.comparison.judge_summary["judged_runs"] == 2
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_validation_cli_run_outputs_experiment_json(monkeypatch) -> None:
    workspace = _workspace("validation-cli")
    try:
        monkeypatch.chdir(workspace)
        suite = Path("validation") / "suites" / "snake.yaml"
        suite.parent.mkdir(parents=True, exist_ok=True)
        suite.write_text(
            "name: snake\n"
            "cases:\n"
            "  - id: snake_design\n"
            "    task: design a snake game plan\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["validate", "run", "snake", "--provider", "mock", "--strategies", "direct_cli,muxdev_single_cli", "--experiment-id", "val_cli", "--json"])

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["experiment_id"] == "val_cli"
        assert payload["strategies"] == ["direct_cli", "muxdev_single_cli"]
        assert payload["comparison"]["winner"] in {"direct_cli", "muxdev_single_cli"}
        assert Path("reports/validation/val_cli.md").exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_validation_api_and_live_dashboard_surface_experiments(monkeypatch) -> None:
    workspace = _workspace("validation-api")
    try:
        validation_dir = workspace / ".muxdev" / "runs" / "val_api" / "validation"
        validation_dir.mkdir(parents=True, exist_ok=True)
        report = workspace / "reports" / "validation" / "val_api.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("# report\n", encoding="utf-8")
        experiment = ValidationExperiment(
            experiment_id="val_api",
            suite=ValidationSuite(name="suite", cases=[]),
            strategies=["direct_cli", "muxdev_multi_cli"],
            comparison=ComparisonReport(experiment_id="val_api", suite="suite", strategy_scores={"direct_cli": 0.5, "muxdev_multi_cli": 0.8}, winner="muxdev_multi_cli", recommendation="Use muxdev"),
            artifacts={"report": str(report)},
        )
        (validation_dir / "experiment.json").write_text(experiment.model_dump_json(indent=2), encoding="utf-8")

        client = TestClient(create_app())
        rows = client.get("/api/validation/experiments", params={"workspace": str(workspace)}).json()
        detail = client.get("/api/validation/experiments/val_api", params={"workspace": str(workspace)}).json()
        html = render_live_dashboard_html()

        assert rows[0]["experiment_id"] == "val_api"
        assert rows[0]["winner"] == "muxdev_multi_cli"
        assert detail["comparison"]["winner"] == "muxdev_multi_cli"
        assert "验证实验" in html
        assert "/api/validation/experiments" in html or "validation.experiments" in html
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _metric(task_id: str, strategy: str, run_id: str, *, score: float, evidence: float, cost: float, tokens: int) -> ValidationMetric:
    return ValidationMetric(
        task_id=task_id,
        strategy=strategy,  # type: ignore[arg-type]
        run_id=run_id,
        status="completed",
        completed=True,
        test_pass_rate=1.0,
        review_blockers=0,
        high_review_blockers=0,
        evidence_confidence=evidence,
        missing_evidence_count=0,
        total_seconds=1.0,
        tokens=tokens,
        cost_usd=cost,
        retry_count=0,
        provider_action_count=0,
        human_intervention_count=0,
        recover_count=0,
        blocked=False,
        rollback_success=True,
        resume_success_rate=1.0,
        plan_test_review_consistency=1.0,
        defect_detection_rate=0.0,
        independent_review_block_rate=0.0,
        security_risk_block_rate=0.0,
        quality_score=score,
        reliability_score=score,
        evidence_score=evidence,
        efficiency_score=score,
        human_effort_inverse=1.0,
        task_completion_score=score,
        answer_quality_score=score,
        process_score=score,
        safety_score=score,
        score=score,
    )
