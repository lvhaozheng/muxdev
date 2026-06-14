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
    single = _metric("task", "single_agent", "run_single", score=0.55, evidence=0.6, cost=0.01, tokens=100)
    multi = _metric("task", "multi_agent", "run_multi", score=0.82, evidence=0.9, cost=0.03, tokens=250)

    report = compare_validation_metrics("val_test", "suite", [single, multi])

    assert report.winner == "multi_agent"
    assert report.strategy_scores["multi_agent"] > report.strategy_scores["single_agent"]
    assert "higher aggregate" in " ".join(report.advantages)
    assert "cost more" in " ".join(report.tradeoffs)


def test_validation_exporters_write_stable_payloads() -> None:
    workspace = _workspace("validation-export")
    try:
        experiment = ValidationExperiment(
            experiment_id="val_export",
            suite=ValidationSuite(name="suite", cases=[]),
            strategies=["single_agent", "multi_agent"],
            runs=[StrategyRun(task_id="task", strategy="single_agent", workflow="single-agent", provider="mock", run_id="run_single", status="completed")],
            metrics=[_metric("task", "single_agent", "run_single", score=0.7, evidence=0.8, cost=0.01, tokens=100)],
            comparison=ComparisonReport(experiment_id="val_export", suite="suite", strategy_scores={"single_agent": 0.7}, winner="single_agent", recommendation="Use single_agent"),
        )
        local = LocalValidationTraceExporter().export_experiment(experiment, workspace / "local")
        langfuse = LangfusePayloadExporter().export_experiment(experiment, workspace / "langfuse")
        langsmith = LangSmithPayloadExporter().export_experiment(experiment, workspace / "langsmith")

        assert local.path.name == "otel_spans.json"
        assert local.spans >= 3
        assert json.loads(langfuse.path.read_text(encoding="utf-8"))["traces"][0]["name"] == "validation.experiment"
        assert json.loads(langsmith.path.read_text(encoding="utf-8"))["project_name"] == "muxdev-validation"
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
        assert {run.strategy for run in experiment.runs} == {"single_agent", "multi_agent"}
        assert len(experiment.metrics) == 2
        assert experiment.comparison is not None
        report = workspace / "reports" / "validation" / "val_snake.md"
        assert report.exists()
        assert "Strategy Scores" in report.read_text(encoding="utf-8")
        assert (workspace / ".muxdev" / "runs" / "val_snake" / "validation" / "experiment.json").exists()
        assert (workspace / ".muxdev" / "runs" / "val_snake" / "validation" / "exports" / "local" / "otel_spans.json").exists()
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

        result = runner.invoke(app, ["validate", "run", "snake", "--provider", "mock", "--experiment-id", "val_cli", "--json"])

        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["experiment_id"] == "val_cli"
        assert payload["comparison"]["winner"] in {"single_agent", "multi_agent"}
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
            strategies=["single_agent", "multi_agent"],
            comparison=ComparisonReport(experiment_id="val_api", suite="suite", strategy_scores={"single_agent": 0.5, "multi_agent": 0.8}, winner="multi_agent", recommendation="Use multi_agent"),
            artifacts={"report": str(report)},
        )
        (validation_dir / "experiment.json").write_text(experiment.model_dump_json(indent=2), encoding="utf-8")

        client = TestClient(create_app())
        rows = client.get("/api/validation/experiments", params={"workspace": str(workspace)}).json()
        detail = client.get("/api/validation/experiments/val_api", params={"workspace": str(workspace)}).json()
        html = render_live_dashboard_html()

        assert rows[0]["experiment_id"] == "val_api"
        assert rows[0]["winner"] == "multi_agent"
        assert detail["comparison"]["winner"] == "multi_agent"
        assert "Validation Experiments" in html
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
        score=score,
    )
