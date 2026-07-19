"""Deterministic, certification-aware task routing."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..core.redaction import redact
from ..domain.routing import RouteCandidate, RouteDecision, TaskFeatureSet
from ..domain.run import HarnessPolicySpec, RoutingPolicySpec
from ..providers.certification import certification_is_current, make_certification_report
from ..providers.harness import AdapterProbe, CertificationStatus, TrustTier, capability_map


IGNORED_DIRECTORIES = frozenset({
    ".git", ".muxdev", ".hg", ".svn", ".venv", "venv", "node_modules",
    "dist", "build", "target", "__pycache__", ".pytest_cache", ".mypy_cache",
})
LANGUAGE_EXTENSIONS = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".rs": "rust", ".go": "go",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".cs": "csharp",
    ".cpp": "cpp", ".cc": "cpp", ".c": "c", ".h": "c", ".hpp": "cpp",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".scala": "scala",
    ".sh": "shell", ".ps1": "powershell", ".sql": "sql", ".md": "markdown",
}
TEST_MARKERS = {
    "pytest.ini": "pytest", "pyproject.toml": "pytest", "jest.config.js": "jest",
    "jest.config.ts": "jest", "vitest.config.ts": "vitest", "go.mod": "go-test",
    "Cargo.toml": "cargo-test", "pom.xml": "junit", "build.gradle": "junit",
}
BUILD_MARKERS = {
    "pyproject.toml": "python-build", "setup.py": "python-build", "package.json": "node",
    "Cargo.toml": "cargo", "go.mod": "go", "pom.xml": "maven",
    "build.gradle": "gradle", "CMakeLists.txt": "cmake", "Makefile": "make",
}


def extract_task_features(
    *,
    run_id: str,
    task: str,
    workspace: Path,
    harness_policy: HarnessPolicySpec,
    routing_policy: RoutingPolicySpec,
    automation: Mapping[str, object] | None = None,
) -> TaskFeatureSet:
    """Extract bounded repository metadata without retaining source text or paths."""
    root = Path(workspace).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"routing workspace is not a directory: {root}")
    language_counts: dict[str, int] = {}
    test_frameworks: set[str] = set()
    build_systems: set[str] = set()
    file_count = 0
    for path in _bounded_files(root):
        file_count += 1
        language = LANGUAGE_EXTENSIONS.get(path.suffix.lower())
        if language:
            language_counts[language] = language_counts.get(language, 0) + 1
        marker = path.name
        if marker in TEST_MARKERS:
            test_frameworks.add(TEST_MARKERS[marker])
        if marker in BUILD_MARKERS:
            build_systems.add(BUILD_MARKERS[marker])
    languages = tuple(
        language for language, _count in sorted(language_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
    )
    task_type = _task_type(task, automation)
    change_scope = _change_scope(file_count, task, automation)
    source_inputs = {
        "task_type": task_type,
        "languages": list(languages),
        "repository_size_bucket": _size_bucket(file_count),
        "repository_file_count": file_count,
        "test_frameworks": sorted(test_frameworks),
        "build_systems": sorted(build_systems),
        "change_scope": change_scope,
        "risk_level": harness_policy.risk_level,
        "risk_tags": sorted(harness_policy.risk_tags),
        "required_capabilities": sorted(harness_policy.required_capabilities),
        "isolation_requirement": harness_policy.isolation_requirement,
        "minimum_certification": harness_policy.minimum_certification,
        "delivery_mode": routing_policy.delivery_mode,
        "budget_limit_usd": routing_policy.max_cost_usd,
    }
    return TaskFeatureSet.create(run_id=run_id, _source_material={"task": redact(task)}, **source_inputs)


def route_task(
    board: Any,
    *,
    feature_set: TaskFeatureSet,
    routing_policy: RoutingPolicySpec,
    harness_policy: HarnessPolicySpec,
    adapters: Mapping[str, object],
    idempotency_key: str = "route:main:v1",
    supersedes_decision_id: str | None = None,
) -> RouteDecision:
    existing = board.get_route_decision(feature_set.run_id, idempotency_key=idempotency_key)
    if existing is not None:
        return board.route_decision_from_row(existing)
    candidates = tuple(
        _candidate_for(
            board,
            provider=provider,
            adapter=adapter,
            feature_set=feature_set,
            routing_policy=routing_policy,
            harness_policy=harness_policy,
        )
        for provider, adapter in sorted(adapters.items())
    )
    main = _select_main(candidates, routing_policy)
    reviewer_required = routing_policy.reviewer_policy == "required" or harness_policy.risk_level == "high"
    reviewer = _select_reviewer(candidates, main, routing_policy) if reviewer_required and main else None
    reasons: list[str] = []
    if routing_policy.mode == "fixed":
        reasons.append("explicit_fixed_provider")
    else:
        reasons.append("capability_constrained_quality_route")
    if main and main.sample_count < routing_policy.minimum_samples:
        reasons.append("insufficient_quality_evidence")
    if main and not main.eligible:
        reasons.append("fixed_provider_requires_policy_waiver")
    if main is None:
        reasons.append("no_eligible_main_provider")
    if reviewer_required and reviewer is None:
        reasons.append("heterogeneous_reviewer_unavailable")
    snapshot = board.latest_benchmark_snapshot(routing_policy.benchmark_snapshot_id)
    previous = board.latest_route_decision(feature_set.run_id)
    decision = RouteDecision.create(
        run_id=feature_set.run_id,
        decision_kind="main",
        idempotency_key=idempotency_key,
        feature_set_id=feature_set.feature_set_id,
        feature_hash=feature_set.source_hash,
        candidates=candidates,
        selected_main_provider=main.provider if main else None,
        selected_reviewer_provider=reviewer.provider if reviewer else None,
        benchmark_snapshot_id=str(snapshot["snapshot_id"]) if snapshot else None,
        benchmark_snapshot_hash=str(snapshot["snapshot_hash"]) if snapshot else None,
        reason_codes=tuple(reasons),
        routing_policy=routing_policy.to_payload(),
        supersedes_decision_id=supersedes_decision_id,
        prev_hash=str(previous["decision_hash"]) if previous else None,
    )
    return board.record_route_decision(decision)


def replay_route(
    board: Any,
    *,
    run_id: str,
    benchmark_snapshot_id: str | None = None,
) -> dict[str, object]:
    original = board.latest_route_decision(run_id, kind="main")
    features = board.latest_task_feature_set(run_id)
    if original is None or features is None:
        raise FileNotFoundError(f"routing facts not found for run: {run_id}")
    candidates = tuple(board.route_candidate_from_payload(item) for item in original.get("candidates", []))
    routing_payload = original.get("routing_policy") or {}
    policy = RoutingPolicySpec.from_payload(routing_payload if isinstance(routing_payload, Mapping) else {})
    if benchmark_snapshot_id:
        policy = replace(policy, benchmark_snapshot_id=benchmark_snapshot_id)
    selected = _select_main(candidates, policy)
    reviewer_required = policy.reviewer_policy == "required" or str(features.get("risk_level") or "") == "high"
    reviewer = _select_reviewer(candidates, selected, policy) if selected and reviewer_required else None
    replay_payload = {
        "run_id": run_id,
        "simulation": True,
        "production_evidence": False,
        "original_decision_id": original.get("decision_id"),
        "original_main_provider": original.get("selected_main_provider"),
        "original_reviewer_provider": original.get("selected_reviewer_provider"),
        "replayed_main_provider": selected.provider if selected else None,
        "replayed_reviewer_provider": reviewer.provider if reviewer else None,
        "feature_hash": features.get("source_hash"),
        "candidate_snapshot_hash": _candidate_snapshot_hash(candidates),
        "benchmark_snapshot_id": benchmark_snapshot_id or original.get("benchmark_snapshot_id"),
    }
    replay_payload["replay_hash"] = _mapping_hash(replay_payload)
    return replay_payload


def beta_quality_lower_bound(successes: float, failures: float, *, z: float = 1.2815515655446004) -> tuple[float, float, float]:
    """Return Jeffreys posterior alpha/beta and a conservative 10% bound."""
    alpha = 0.5 + max(0.0, float(successes))
    beta = 0.5 + max(0.0, float(failures))
    total = alpha + beta
    mean = alpha / total
    variance = (alpha * beta) / ((total**2) * (total + 1.0))
    lower = max(0.0, min(1.0, mean - z * math.sqrt(variance)))
    return alpha, beta, lower


def _candidate_for(
    board: Any,
    *,
    provider: str,
    adapter: object,
    feature_set: TaskFeatureSet,
    routing_policy: RoutingPolicySpec,
    harness_policy: HarnessPolicySpec,
) -> RouteCandidate:
    probe_method = getattr(adapter, "probe", None)
    certify_method = getattr(adapter, "certify", None)
    probe = probe_method() if callable(probe_method) else AdapterProbe(
        provider=provider,
        available=True,
        executable=None,
        provider_version=None,
        executable_fingerprint=None,
        help_fingerprint=None,
        adapter_version=str(getattr(adapter, "adapter_version", "legacy-generic/1")),
        platform="unknown",
        diagnostics=("legacy adapter has no certification lifecycle",),
    )
    report = _current_certification(board, provider, probe)
    if report is None and callable(certify_method):
        report = board.record_adapter_certification(certify_method(live=False))
    if report is None:
        fallback = make_certification_report(
            probe=probe,
            capabilities=capability_map(),
            trust_tier=TrustTier.OPAQUE,
            live=False,
            evidence={"checks": ["uncertified legacy adapter"]},
            failure="adapter is not certifiable",
        )
        report = board.record_adapter_certification(fallback)
    capabilities = report.get("capabilities") if isinstance(report.get("capabilities"), Mapping) else {}
    verified = tuple(sorted(str(name) for name, state in capabilities.items() if str(state) == "verified"))
    trust_tier = str(report.get("trust_tier") or getattr(adapter, "trust_tier", "opaque"))
    managed = trust_tier == str(TrustTier.MANAGED)
    isolation = "managed" if managed else ("provider_sandbox" if "provider_sandbox" in verified else "process")
    delivery_modes = ("simulation",) if provider in {"mock", "replay"} else ("production",)
    exclusions: list[str] = []
    if not bool(probe.available):
        exclusions.append("unavailable")
    if not _certification_satisfies(str(report.get("status") or "uncertified"), harness_policy.minimum_certification, managed=managed):
        exclusions.append("uncertified" if str(report.get("status")) != "stale" else "stale_certification")
    if not set(harness_policy.required_capabilities).issubset(verified):
        exclusions.append("capability_missing")
    if not _isolation_satisfies(harness_policy.isolation_requirement, isolation, verified, managed=managed):
        exclusions.append("isolation_mismatch")
    if routing_policy.delivery_mode not in delivery_modes:
        exclusions.append("unsupported_delivery_mode")
    if routing_policy.allowed_providers and provider not in routing_policy.allowed_providers:
        exclusions.append("risk_policy_mismatch")
    outcomes = board.routing_outcomes(
        provider=provider,
        task_type=feature_set.task_type,
        evidence_complete=True,
        source="simulation" if feature_set.delivery_mode == "simulation" else "production",
    )
    successes = sum(float(row.get("verified_success") or 0.0) for row in outcomes)
    failures = max(0.0, float(len(outcomes)) - successes)
    alpha, beta, lower = beta_quality_lower_bound(successes, failures)
    cost = _percentile([float(row.get("cost_usd") or 0.0) for row in outcomes], 0.9)
    latency = _percentile([float(row.get("latency_seconds") or 0.0) for row in outcomes], 0.9)
    if cost > routing_policy.max_cost_usd:
        exclusions.append("budget_exceeded")
    return RouteCandidate(
        provider=provider,
        adapter_version=str(report.get("adapter_version") or getattr(adapter, "adapter_version", "unknown")),
        provider_version=str(report["provider_version"]) if report.get("provider_version") else None,
        executable_fingerprint=str(getattr(probe, "executable_fingerprint", "") or "") or None,
        trust_tier=trust_tier,
        certification_id=str(report["certification_id"]) if report.get("certification_id") else None,
        certification_status=str(report.get("status") or CertificationStatus.UNCERTIFIED),
        verified_capabilities=verified,
        actual_isolation=isolation,
        delivery_modes=delivery_modes,
        eligible=not exclusions,
        exclusion_codes=tuple(sorted(set(exclusions))),
        quality_alpha=round(alpha, 6),
        quality_beta=round(beta, 6),
        quality_lower_bound=round(lower, 6),
        sample_count=len(outcomes),
        estimated_cost_p90=round(cost, 6),
        estimated_latency_p90=round(latency, 3),
    )


def _current_certification(board: Any, provider: str, probe: AdapterProbe) -> dict[str, object] | None:
    for report in board.list_adapter_certifications(provider=provider):
        if certification_is_current(report, probe):
            return report
    return None


def _select_main(candidates: tuple[RouteCandidate, ...], policy: RoutingPolicySpec) -> RouteCandidate | None:
    eligible = [candidate for candidate in candidates if candidate.eligible]
    if policy.mode == "fixed":
        requested = next((candidate for candidate in candidates if candidate.provider == policy.fixed_provider), None)
        if requested and not ({"unavailable", "unsupported_delivery_mode", "budget_exceeded"} & set(requested.exclusion_codes)):
            # Fixed selection is still subject to the Harness preflight.  It is
            # recorded here so a subject-bound downgrade Approval can be
            # created instead of silently switching to another Provider.
            return requested
        return None
    if not eligible:
        return None
    preferred = next((candidate for candidate in eligible if candidate.provider == policy.fixed_provider), None)
    if preferred and all(candidate.sample_count < policy.minimum_samples for candidate in eligible):
        return preferred
    return sorted(
        eligible,
        key=lambda item: (
            -item.quality_lower_bound,
            -item.sample_count,
            item.estimated_cost_p90,
            item.estimated_latency_p90,
            item.provider,
        ),
    )[0]


def _select_reviewer(
    candidates: tuple[RouteCandidate, ...],
    main: RouteCandidate | None,
    policy: RoutingPolicySpec,
) -> RouteCandidate | None:
    if main is None:
        return None
    eligible = [
        candidate for candidate in candidates
        if candidate.eligible
        and candidate.provider != main.provider
        and "read_only" in candidate.verified_capabilities
        and main.estimated_cost_p90 + candidate.estimated_cost_p90 <= policy.max_cost_usd
        and (
            not candidate.executable_fingerprint
            or not main.executable_fingerprint
            or candidate.executable_fingerprint != main.executable_fingerprint
        )
    ]
    return sorted(eligible, key=lambda item: (-item.quality_lower_bound, item.estimated_cost_p90, item.provider))[0] if eligible else None


def _certification_satisfies(actual: str, required: str, *, managed: bool) -> bool:
    levels = {"unavailable": 0, "uncertified": 0, "failed": 0, "stale": 0, "offline_verified": 1, "live_verified": 2}
    if managed and levels.get(actual, 0) >= 1:
        return True
    return levels.get(actual, 0) >= levels.get(required, 1)


def _isolation_satisfies(required: str, actual: str, verified: tuple[str, ...], *, managed: bool) -> bool:
    if managed:
        return True
    if required == "provider_sandbox":
        return actual == "provider_sandbox"
    return actual in {"provider_sandbox", "container"} and "cooperative_cancel" in verified


def _bounded_files(root: Path, *, maximum: int = 100_000) -> Iterable[Path]:
    count = 0
    stack = [root]
    while stack and count < maximum:
        directory = stack.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name.lower())
        except (OSError, PermissionError):
            continue
        for child in children:
            if child.is_symlink():
                continue
            if child.is_dir():
                if child.name not in IGNORED_DIRECTORIES:
                    stack.append(child)
                continue
            if child.is_file():
                count += 1
                yield child
                if count >= maximum:
                    return


def _task_type(task: str, automation: Mapping[str, object] | None) -> str:
    intent = str((automation or {}).get("intent") or "").strip().lower()
    if intent in {"fix", "dev", "refactor", "test", "docs", "design", "review"}:
        return {"fix": "bugfix", "dev": "feature"}.get(intent, intent)
    lowered = task.lower()
    for kind, terms in (
        ("security", ("security", "vulnerability", "安全", "漏洞")),
        ("migration", ("migration", "migrate", "迁移")),
        ("test", ("test", "pytest", "测试")),
        ("docs", ("docs", "readme", "文档")),
        ("refactor", ("refactor", "重构")),
        ("bugfix", ("fix", "bug", "修复", "报错")),
    ):
        if any(term in lowered for term in terms):
            return kind
    return "feature"


def _change_scope(file_count: int, task: str, automation: Mapping[str, object] | None) -> str:
    complexity = str(((automation or {}).get("intake") or {}).get("complexity") or "") if isinstance((automation or {}).get("intake"), Mapping) else ""
    lowered = task.lower()
    if complexity == "complex" or any(term in lowered for term in ("architecture", "cross-module", "架构", "全局")):
        return "large"
    if complexity == "light" or any(term in lowered for term in ("tiny", "small", "typo", "简单", "小修")):
        return "small"
    return "large" if file_count > 20_000 else "medium"


def _size_bucket(file_count: int) -> str:
    if file_count < 100:
        return "tiny"
    if file_count < 1_000:
        return "small"
    if file_count < 10_000:
        return "medium"
    if file_count < 50_000:
        return "large"
    return "very_large"


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def _candidate_snapshot_hash(candidates: tuple[RouteCandidate, ...]) -> str:
    return _mapping_hash({"candidates": [candidate.to_dict() for candidate in candidates]})


def _mapping_hash(value: Mapping[str, object]) -> str:
    import hashlib
    import json

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
