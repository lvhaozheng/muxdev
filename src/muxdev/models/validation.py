"""Validation experiment contracts for comparing agent strategies."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from . import utc_now


ValidationStrategy = Literal["direct_cli", "muxdev_single_cli", "muxdev_multi_cli", "single_agent", "multi_agent"]


class ValidationCase(BaseModel):
    id: str
    task: str
    fixture: str | None = None
    tags: list[str] = Field(default_factory=list)


class ValidationSuite(BaseModel):
    name: str
    description: str = ""
    cases: list[ValidationCase]


class StrategyRun(BaseModel):
    task_id: str
    fixture: str | None = None
    strategy: ValidationStrategy
    mode: str = "muxdev"
    workflow: str
    provider: str
    role_providers: dict[str, str] = Field(default_factory=dict)
    run_id: str
    baseline_run_id: str | None = None
    status: str
    output_path: str | None = None
    diff_path: str | None = None
    judge_path: str | None = None
    seed_config: dict[str, Any] = Field(default_factory=dict)


class ValidationMetric(BaseModel):
    task_id: str
    strategy: ValidationStrategy
    run_id: str
    status: str
    completed: bool
    test_pass_rate: float
    review_blockers: int
    high_review_blockers: int
    evidence_confidence: float
    missing_evidence_count: int
    total_seconds: float
    stage_seconds: dict[str, float] = Field(default_factory=dict)
    tokens: int
    cost_usd: float
    retry_count: int
    provider_action_count: int
    human_intervention_count: int
    recover_count: int
    blocked: bool
    rollback_success: bool
    stage_failure_kinds: list[str] = Field(default_factory=list)
    resume_success_rate: float
    plan_test_review_consistency: float
    defect_detection_rate: float
    independent_review_block_rate: float
    security_risk_block_rate: float
    quality_score: float
    reliability_score: float
    evidence_score: float
    efficiency_score: float
    human_effort_inverse: float
    task_completion_score: float = 0.0
    answer_quality_score: float = 0.0
    process_score: float = 0.0
    safety_score: float = 0.0
    workflow_engine: str = "native"
    loop_iterations: int = 0
    loop_blocked: bool = False
    retrieval_used: bool = False
    citation_coverage: float = 0.0
    retrieval_hit_rate: float = 0.0
    checkpoint_recovery: float = 1.0
    judge_score: float | None = None
    judge_pass: bool | None = None
    judge_reasons: list[str] = Field(default_factory=list)
    score: float


class ComparisonReport(BaseModel):
    experiment_id: str
    suite: str
    strategy_scores: dict[str, float]
    winner: str
    recommendation: str
    advantages: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    failure_cases: list[dict[str, Any]] = Field(default_factory=list)
    cost_delta_usd: float = 0.0
    token_delta: int = 0
    baseline_strategy: str = "direct_cli"
    muxdev_delta: dict[str, float] = Field(default_factory=dict)
    judge_summary: dict[str, Any] = Field(default_factory=dict)


class ValidationExperiment(BaseModel):
    contract_version: str = "muxdev.validation.v1"
    experiment_id: str
    suite: ValidationSuite
    strategies: list[ValidationStrategy]
    runs: list[StrategyRun] = Field(default_factory=list)
    metrics: list[ValidationMetric] = Field(default_factory=list)
    comparison: ComparisonReport | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
