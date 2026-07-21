"""Small, explainable provider router backed only by verified outcomes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..config.loader import load_config
from ..providers import CapabilityState, certification_matches, probe_provider
from ..providers.capabilities import provider_capabilities
from ..storage.control import ControlStore


@dataclass(frozen=True)
class RoutedProvider:
    provider: str
    eligible: bool
    reasons: tuple[str, ...]
    successes: int
    failures: int
    beta_lower_bound: float
    cost_p90: float
    latency_p90: float
    score: float

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "successes": self.successes,
            "failures": self.failures,
            "beta_lower_bound": self.beta_lower_bound,
            "cost_p90": self.cost_p90,
            "latency_p90": self.latency_p90,
            "score": self.score,
        }


def beta_lower_bound(successes: int, failures: int, *, z: float = 1.2815515655446004) -> float:
    alpha = 0.5 + max(successes, 0)
    beta = 0.5 + max(failures, 0)
    total = alpha + beta
    mean = alpha / total
    variance = alpha * beta / ((total**2) * (total + 1))
    return round(max(0.0, mean - z * math.sqrt(variance)), 6)


class ProviderRouter:
    def __init__(self, store: ControlStore) -> None:
        self.store = store

    def route(
        self,
        run_id: str,
        *,
        preferred: str | None,
        profile: str,
        max_cost_usd: float,
        roles: Sequence[str] = (),
        role_providers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        overrides = dict(role_providers or {})
        candidates = self._candidates(
            preferred=preferred,
            additional=tuple(overrides.values()),
            profile=profile,
            max_cost_usd=max_cost_usd,
        )
        eligible = [item for item in candidates if item.eligible]
        candidates_by_name = {item.provider: item for item in candidates}
        eligible_by_name = {item.provider: item for item in eligible}
        implementation_provider = overrides.get("code") or preferred
        if implementation_provider:
            main = next(
                (item for item in eligible if item.provider == implementation_provider), None
            )
        else:
            main = max(eligible, key=lambda item: (item.score, item.provider), default=None)
        reviewer = None
        if profile in {"standard", "strict"} and main and main.provider not in {"mock", "replay"}:
            reviewer = max(
                (item for item in eligible if item.provider != main.provider),
                key=lambda item: (item.score, item.provider),
                default=None,
            )
        role_errors: list[str] = []
        role_assignments: dict[str, str] = {}
        for role in dict.fromkeys(roles):
            requested = overrides.get(role)
            if requested:
                if requested not in eligible_by_name:
                    candidate = candidates_by_name.get(requested)
                    reasons = ", ".join(candidate.reasons) if candidate else "not configured"
                    role_errors.append(
                        f"{role} Provider '{requested}' is not eligible: {reasons}"
                    )
                    continue
                if (
                    profile in {"standard", "strict"}
                    and role in {"review", "secure"}
                    and main
                    and requested == main.provider
                ):
                    role_errors.append(
                        f"{role} Provider must be independent from executor '{main.provider}'"
                    )
                    continue
                role_assignments[role] = requested
            elif role in {"review", "secure"} and reviewer:
                role_assignments[role] = reviewer.provider
            elif main:
                role_assignments[role] = main.provider
        resolved_reviewer = role_assignments.get("review") or (
            reviewer.provider if reviewer else None
        )
        payload = {
            "main_provider": main.provider if main else None,
            "reviewer_provider": resolved_reviewer,
            "profile": profile,
            "reason": "capability_and_history" if main else "no_eligible_provider",
            "candidates": [item.to_dict() for item in candidates],
            "warnings": [],
            "role_assignments": role_assignments,
            "role_errors": role_errors,
        }
        if main and main.provider not in {"mock", "replay"}:
            certification = self.store.latest_certification(main.provider)
            if (
                not certification
                or certification.get("status") != "live_verified"
                or not certification_matches(self.store.workspace, main.provider, certification)
            ):
                payload["warnings"].append("selected provider is not fingerprint-matched live-certified")
        payload["decision_id"] = self.store.record_routing(run_id, payload)
        return payload

    def explain(self, run_id: str) -> dict[str, Any]:
        decisions = self.store.routing(run_id)
        if not decisions:
            raise FileNotFoundError(f"routing decision not found: {run_id}")
        return decisions[-1]

    def replay(self, run_id: str) -> dict[str, Any]:
        original = self.explain(run_id)
        payload = original.get("payload") if isinstance(original.get("payload"), dict) else {}
        candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        eligible = [item for item in candidates if isinstance(item, dict) and item.get("eligible")]
        selected = max(eligible, key=lambda item: (float(item.get("score") or 0), str(item.get("provider"))), default=None)
        return {
            "run_id": run_id,
            "simulation": True,
            "original_provider": payload.get("main_provider"),
            "replayed_provider": selected.get("provider") if selected else None,
            "candidate_count": len(candidates),
        }

    def benchmark(self) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.store.outcomes():
            grouped.setdefault(str(row["provider"]), []).append(row)
        return {provider: self._history(provider, rows) for provider, rows in sorted(grouped.items())}

    def _candidates(
        self,
        *,
        preferred: str | None,
        additional: Sequence[str] = (),
        profile: str,
        max_cost_usd: float,
    ) -> list[RoutedProvider]:
        configured = load_config(self.store.workspace).get("providers", {})
        provider_ids = sorted(str(item) for item in configured) if isinstance(configured, Mapping) else []
        for provider in (preferred, *additional):
            if provider and provider not in provider_ids:
                provider_ids.append(provider)
        return [self._candidate(provider, profile=profile, max_cost_usd=max_cost_usd) for provider in provider_ids]

    def _candidate(self, provider: str, *, profile: str, max_cost_usd: float) -> RoutedProvider:
        reasons: list[str] = []
        certification = self.store.latest_certification(provider)
        live_certified = bool(
            provider in {"mock", "replay"}
            or (
                certification
                and certification.get("status") == "live_verified"
                and certification_matches(self.store.workspace, provider, certification)
            )
        )
        capabilities: dict[str, object] = {}
        try:
            capabilities = provider_capabilities(self.store.workspace, provider)
        except ValueError:
            reasons.append("provider_capabilities_unavailable")
        try:
            probe = probe_provider(provider, workspace=self.store.workspace)
            if not probe.installed:
                reasons.append("unavailable_or_not_authenticated")
            if not live_certified and capabilities.get("protocol") != "acp" and probe.headless != CapabilityState.SUPPORTED:
                reasons.append("headless_execution_not_verified")
            if not live_certified and capabilities.get("protocol") != "acp" and probe.json != CapabilityState.SUPPORTED:
                reasons.append("structured_output_not_verified")
        except (OSError, ValueError) as exc:
            reasons.append(f"unavailable:{type(exc).__name__}")
        if provider not in {"mock", "replay"} and profile in {"standard", "strict"}:
            if not capabilities.get("isolated_config"):
                reasons.append("isolated_provider_config_required")
        if provider not in {"mock", "replay"} and profile in {"standard", "strict"}:
            if not certification or certification.get("status") != "live_verified":
                reasons.append("live_certification_required")
            elif not live_certified:
                reasons.append("certification_fingerprint_mismatch")
        history = self._history(provider, self.store.outcomes(provider))
        if float(history["cost_p90"]) > max_cost_usd:
            reasons.append("cost_limit")
        quality = float(history["beta_lower_bound"])
        cost_factor = 1 / (1 + float(history["cost_p90"]))
        latency_factor = 1 / (1 + float(history["latency_p90"]) / 60)
        score = round(0.6 * quality + 0.2 * cost_factor + 0.2 * latency_factor, 6)
        return RoutedProvider(
            provider=provider,
            eligible=not reasons,
            reasons=tuple(reasons),
            successes=int(history["successes"]),
            failures=int(history["failures"]),
            beta_lower_bound=quality,
            cost_p90=float(history["cost_p90"]),
            latency_p90=float(history["latency_p90"]),
            score=score,
        )

    @staticmethod
    def _history(provider: str, rows: list[dict[str, Any]]) -> dict[str, object]:
        del provider
        trusted = []
        for row in rows:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            if payload.get("gate_status") == "PASS" and payload.get("integrity_valid") is True and payload.get("independent_valid") is True:
                trusted.append(payload)
        successes = sum(bool(item.get("success")) for item in trusted)
        failures = len(trusted) - successes
        return {
            "successes": successes,
            "failures": failures,
            "beta_lower_bound": beta_lower_bound(successes, failures),
            "cost_p90": _percentile([float(item.get("cost_usd") or 0) for item in trusted], 0.9),
            "latency_p90": _percentile([float(item.get("latency_seconds") or 0) for item in trusted], 0.9),
        }


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    values.sort()
    index = max(0, min(len(values) - 1, math.ceil(len(values) * quantile) - 1))
    return round(values[index], 6)
