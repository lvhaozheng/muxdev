"""Versioned task-level routing and heterogeneous review contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4


ROUTING_CONTRACT_VERSION = "muxdev.routing.v1"
ROUTING_POLICY_VERSION = "muxdev.routing-policy/1"
ROUTING_SCORER_VERSION = "muxdev.beta-quality/1"
FEATURE_EXTRACTOR_VERSION = "muxdev.task-features/1"
REVIEW_CONTRACT_VERSION = "muxdev.heterogeneous-review.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Mapping[str, Any] | list[Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class TaskFeatureSet:
    feature_set_id: str
    run_id: str
    task_type: str
    languages: tuple[str, ...]
    repository_size_bucket: str
    repository_file_count: int
    test_frameworks: tuple[str, ...]
    build_systems: tuple[str, ...]
    change_scope: str
    risk_level: str
    risk_tags: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    isolation_requirement: str
    minimum_certification: str
    delivery_mode: str
    budget_limit_usd: float
    extractor_version: str = FEATURE_EXTRACTOR_VERSION
    source_hash: str = ""
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def create(cls, **values: Any) -> "TaskFeatureSet":
        source_material = values.pop("_source_material", {})
        item = cls(feature_set_id=str(values.pop("feature_set_id", f"tfs_{uuid4().hex}")), **values)
        payload = item.to_dict()
        payload.pop("feature_set_id", None)
        payload.pop("created_at", None)
        payload.pop("source_hash", None)
        payload["source_material"] = source_material
        return replace(item, source_hash=canonical_hash(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_set_id": self.feature_set_id,
            "contract_version": ROUTING_CONTRACT_VERSION,
            "run_id": self.run_id,
            "task_type": self.task_type,
            "languages": list(self.languages),
            "repository_size_bucket": self.repository_size_bucket,
            "repository_file_count": self.repository_file_count,
            "test_frameworks": list(self.test_frameworks),
            "build_systems": list(self.build_systems),
            "change_scope": self.change_scope,
            "risk_level": self.risk_level,
            "risk_tags": list(self.risk_tags),
            "required_capabilities": list(self.required_capabilities),
            "isolation_requirement": self.isolation_requirement,
            "minimum_certification": self.minimum_certification,
            "delivery_mode": self.delivery_mode,
            "budget_limit_usd": self.budget_limit_usd,
            "extractor_version": self.extractor_version,
            "source_hash": self.source_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaskFeatureSet":
        return cls(
            feature_set_id=str(payload["feature_set_id"]), run_id=str(payload["run_id"]),
            task_type=str(payload["task_type"]), languages=tuple(str(x) for x in payload.get("languages", [])),
            repository_size_bucket=str(payload["repository_size_bucket"]),
            repository_file_count=int(payload.get("repository_file_count") or 0),
            test_frameworks=tuple(str(x) for x in payload.get("test_frameworks", [])),
            build_systems=tuple(str(x) for x in payload.get("build_systems", [])),
            change_scope=str(payload["change_scope"]), risk_level=str(payload["risk_level"]),
            risk_tags=tuple(str(x) for x in payload.get("risk_tags", [])),
            required_capabilities=tuple(str(x) for x in payload.get("required_capabilities", [])),
            isolation_requirement=str(payload["isolation_requirement"]),
            minimum_certification=str(payload["minimum_certification"]),
            delivery_mode=str(payload["delivery_mode"]), budget_limit_usd=float(payload.get("budget_limit_usd") or 0.0),
            extractor_version=str(payload.get("extractor_version") or FEATURE_EXTRACTOR_VERSION),
            source_hash=str(payload.get("source_hash") or ""), created_at=str(payload.get("created_at") or utc_now()),
        )


@dataclass(frozen=True)
class RouteCandidate:
    provider: str
    adapter_version: str
    provider_version: str | None
    executable_fingerprint: str | None
    trust_tier: str
    certification_id: str | None
    certification_status: str
    verified_capabilities: tuple[str, ...]
    actual_isolation: str
    delivery_modes: tuple[str, ...]
    eligible: bool
    exclusion_codes: tuple[str, ...] = ()
    quality_alpha: float = 0.5
    quality_beta: float = 0.5
    quality_lower_bound: float = 0.0
    sample_count: int = 0
    estimated_cost_p90: float = 0.0
    estimated_latency_p90: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "adapter_version": self.adapter_version,
            "provider_version": self.provider_version,
            "executable_fingerprint": self.executable_fingerprint,
            "trust_tier": self.trust_tier,
            "certification_id": self.certification_id,
            "certification_status": self.certification_status,
            "verified_capabilities": list(self.verified_capabilities),
            "actual_isolation": self.actual_isolation,
            "delivery_modes": list(self.delivery_modes),
            "eligible": self.eligible,
            "exclusion_codes": list(self.exclusion_codes),
            "quality_posterior": {"alpha": self.quality_alpha, "beta": self.quality_beta},
            "quality_lower_bound": self.quality_lower_bound,
            "sample_count": self.sample_count,
            "estimated_cost_p90": self.estimated_cost_p90,
            "estimated_latency_p90": self.estimated_latency_p90,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RouteCandidate":
        posterior = payload.get("quality_posterior") if isinstance(payload.get("quality_posterior"), Mapping) else {}
        return cls(
            provider=str(payload["provider"]), adapter_version=str(payload.get("adapter_version") or "unknown"),
            provider_version=str(payload["provider_version"]) if payload.get("provider_version") else None,
            executable_fingerprint=str(payload["executable_fingerprint"]) if payload.get("executable_fingerprint") else None,
            trust_tier=str(payload.get("trust_tier") or "opaque"),
            certification_id=str(payload["certification_id"]) if payload.get("certification_id") else None,
            certification_status=str(payload.get("certification_status") or "uncertified"),
            verified_capabilities=tuple(str(x) for x in payload.get("verified_capabilities", [])),
            actual_isolation=str(payload.get("actual_isolation") or "process"),
            delivery_modes=tuple(str(x) for x in payload.get("delivery_modes", [])),
            eligible=bool(payload.get("eligible")), exclusion_codes=tuple(str(x) for x in payload.get("exclusion_codes", [])),
            quality_alpha=float(posterior.get("alpha") or 0.5), quality_beta=float(posterior.get("beta") or 0.5),
            quality_lower_bound=float(payload.get("quality_lower_bound") or 0.0), sample_count=int(payload.get("sample_count") or 0),
            estimated_cost_p90=float(payload.get("estimated_cost_p90") or 0.0),
            estimated_latency_p90=float(payload.get("estimated_latency_p90") or 0.0),
        )


@dataclass(frozen=True)
class RouteDecision:
    decision_id: str
    run_id: str
    decision_kind: str
    idempotency_key: str
    feature_set_id: str
    feature_hash: str
    candidates: tuple[RouteCandidate, ...]
    selected_main_provider: str | None
    selected_reviewer_provider: str | None
    benchmark_snapshot_id: str | None
    benchmark_snapshot_hash: str | None
    reason_codes: tuple[str, ...]
    routing_policy: Mapping[str, Any] = field(default_factory=dict)
    policy_version: str = ROUTING_POLICY_VERSION
    scorer_version: str = ROUTING_SCORER_VERSION
    decision_version: int = 1
    supersedes_decision_id: str | None = None
    prev_hash: str | None = None
    decision_hash: str = ""
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def create(cls, **values: Any) -> "RouteDecision":
        item = cls(decision_id=str(values.pop("decision_id", f"route_{uuid4().hex}")), **values)
        payload = item.to_dict()
        payload.pop("decision_hash", None)
        return replace(item, decision_hash=canonical_hash(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "contract_version": ROUTING_CONTRACT_VERSION,
            "run_id": self.run_id,
            "decision_kind": self.decision_kind,
            "decision_version": self.decision_version,
            "idempotency_key": self.idempotency_key,
            "feature_set_id": self.feature_set_id,
            "feature_hash": self.feature_hash,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected_main_provider": self.selected_main_provider,
            "selected_reviewer_provider": self.selected_reviewer_provider,
            "benchmark_snapshot_id": self.benchmark_snapshot_id,
            "benchmark_snapshot_hash": self.benchmark_snapshot_hash,
            "policy_version": self.policy_version,
            "scorer_version": self.scorer_version,
            "reason_codes": list(self.reason_codes),
            "routing_policy": dict(self.routing_policy),
            "supersedes_decision_id": self.supersedes_decision_id,
            "created_at": self.created_at,
            "prev_hash": self.prev_hash,
            "decision_hash": self.decision_hash,
        }


def verify_route_decision_hash(decision: RouteDecision) -> bool:
    payload = decision.to_dict()
    expected = str(payload.pop("decision_hash", ""))
    return bool(expected) and expected == canonical_hash(payload)


@dataclass(frozen=True)
class ReviewAssignment:
    review_id: str
    run_id: str
    route_decision_id: str
    reviewer_provider: str | None
    main_provider: str
    status: str
    snapshot_hash: str | None = None
    attempt: int = 1
    waiver_approval_id: str | None = None
    verdict: str | None = None
    findings: tuple[Mapping[str, Any], ...] = ()
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def create(cls, **values: Any) -> "ReviewAssignment":
        return cls(review_id=str(values.pop("review_id", f"review_{uuid4().hex}")), **values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "contract_version": REVIEW_CONTRACT_VERSION,
            "run_id": self.run_id,
            "route_decision_id": self.route_decision_id,
            "reviewer_provider": self.reviewer_provider,
            "main_provider": self.main_provider,
            "status": self.status,
            "snapshot_hash": self.snapshot_hash,
            "attempt": self.attempt,
            "waiver_approval_id": self.waiver_approval_id,
            "verdict": self.verdict,
            "findings": [dict(item) for item in self.findings],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
