"""Validation observability export adapters.

The v1 exporter is intentionally offline-first: it writes local JSONL and an
OpenTelemetry-compatible span document. Langfuse and LangSmith payloads are
generated as stable artifacts, but no network calls are made by default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..models.validation import ValidationExperiment


@dataclass(frozen=True)
class ExportResult:
    exporter: str
    path: Path
    spans: int
    status: str = "written"


class ValidationTraceExporter(Protocol):
    def export_experiment(self, experiment: ValidationExperiment, output_dir: Path) -> ExportResult:
        """Export an experiment trace artifact."""


class LocalValidationTraceExporter:
    exporter = "local"

    def export_experiment(self, experiment: ValidationExperiment, output_dir: Path) -> ExportResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        spans = validation_spans(experiment)
        jsonl_path = output_dir / "validation_spans.jsonl"
        jsonl_path.write_text("".join(json.dumps(span, ensure_ascii=False, sort_keys=True) + "\n" for span in spans), encoding="utf-8")
        otel_path = output_dir / "otel_spans.json"
        otel_path.write_text(json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}, ensure_ascii=False, indent=2), encoding="utf-8")
        return ExportResult(self.exporter, otel_path, len(spans))


class LangfusePayloadExporter:
    exporter = "langfuse"

    def export_experiment(self, experiment: ValidationExperiment, output_dir: Path) -> ExportResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        spans = validation_spans(experiment)
        payload = {
            "traces": [
                {
                    "id": span["span_id"],
                    "name": span["name"],
                    "parent_id": span.get("parent_span_id"),
                    "metadata": span.get("attributes", {}),
                }
                for span in spans
            ]
        }
        path = output_dir / "langfuse_payload.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return ExportResult(self.exporter, path, len(spans))


class LangSmithPayloadExporter:
    exporter = "langsmith"

    def export_experiment(self, experiment: ValidationExperiment, output_dir: Path) -> ExportResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        spans = validation_spans(experiment)
        payload = {
            "project_name": "muxdev-validation",
            "runs": [
                {
                    "id": span["span_id"],
                    "name": span["name"],
                    "parent_run_id": span.get("parent_span_id"),
                    "extra": span.get("attributes", {}),
                }
                for span in spans
            ],
        }
        path = output_dir / "langsmith_payload.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return ExportResult(self.exporter, path, len(spans))


def validation_spans(experiment: ValidationExperiment) -> list[dict[str, object]]:
    spans: list[dict[str, object]] = []
    baseline_deltas = experiment.comparison.muxdev_delta if experiment.comparison else {}
    experiment_span = {
        "span_id": experiment.experiment_id,
        "name": "validation.experiment",
        "kind": "INTERNAL",
        "attributes": {
            "muxdev.contract_version": experiment.contract_version,
            "muxdev.suite": experiment.suite.name,
            "muxdev.strategies": ",".join(experiment.strategies),
            "muxdev.baseline_strategy": experiment.comparison.baseline_strategy if experiment.comparison else "",
            "muxdev.winner": experiment.comparison.winner if experiment.comparison else "",
            "muxdev.muxdev_delta": json.dumps(experiment.comparison.muxdev_delta if experiment.comparison else {}, sort_keys=True),
        },
    }
    spans.append(experiment_span)
    for run in experiment.runs:
        run_span_id = f"{experiment.experiment_id}:{run.run_id}"
        spans.append(
            {
                "span_id": run_span_id,
                "parent_span_id": experiment.experiment_id,
                "name": f"validation.run.{run.strategy}",
                "kind": "INTERNAL",
                "attributes": {
                    "muxdev.task_id": run.task_id,
                    "muxdev.run_id": run.run_id,
                    "muxdev.strategy": run.strategy,
                    "muxdev.mode": run.mode,
                    "muxdev.workflow": run.workflow,
                    "muxdev.provider": run.provider,
                    "muxdev.status": run.status,
                    "muxdev.output_path": run.output_path or "",
                    "muxdev.diff_path": run.diff_path or "",
                    "muxdev.baseline_delta": float(baseline_deltas.get(str(run.strategy), 0.0)),
                },
            }
        )
        for metric in [row for row in experiment.metrics if row.run_id == run.run_id]:
            metric_span_id = f"{run_span_id}:metrics"
            baseline_delta = float(baseline_deltas.get(str(metric.strategy), 0.0))
            spans.append(
                {
                    "span_id": metric_span_id,
                    "parent_span_id": run_span_id,
                    "name": "validation.metrics",
                    "kind": "INTERNAL",
                    "attributes": {
                        "muxdev.task_id": metric.task_id,
                        "muxdev.run_id": metric.run_id,
                        "muxdev.strategy": metric.strategy,
                        "muxdev.score": metric.score,
                        "muxdev.quality_score": metric.quality_score,
                        "muxdev.reliability_score": metric.reliability_score,
                        "muxdev.evidence_score": metric.evidence_score,
                        "muxdev.task_completion_score": metric.task_completion_score,
                        "muxdev.answer_quality_score": metric.answer_quality_score,
                        "muxdev.process_score": metric.process_score,
                        "muxdev.safety_score": metric.safety_score,
                        "muxdev.judge_score": metric.judge_score,
                        "muxdev.judge_pass": metric.judge_pass,
                        "muxdev.baseline_delta": baseline_delta,
                        "muxdev.cost_usd": metric.cost_usd,
                        "muxdev.tokens": metric.tokens,
                    },
                }
            )
            for stage_id, seconds in metric.stage_seconds.items():
                spans.append(
                    {
                        "span_id": f"{metric_span_id}:stage:{stage_id}",
                        "parent_span_id": metric_span_id,
                        "name": f"validation.stage.{stage_id}",
                        "kind": "INTERNAL",
                        "attributes": {"muxdev.stage_id": stage_id, "muxdev.elapsed_seconds": seconds},
                    }
                )
    return spans
