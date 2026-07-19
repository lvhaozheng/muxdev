"""Certification-aware isolation preflight for Provider execution."""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping

from ..domain.run import HarnessPolicySpec
from ..models import ApprovalStatus
from ..storage.contracts import canonical_hash
from .certification import certification_is_current, make_certification_report
from .harness import AdapterProbe, CertificationStatus, TrustTier, capability_map


@dataclass(frozen=True)
class HarnessPreflight:
    status: str
    subject: Mapping[str, object]
    approval_id: str | None = None
    reason: str = ""


def ensure_harness_policy(
    board: Any,
    *,
    run_id: str,
    adapters: Mapping[str, object],
    policy: HarnessPolicySpec,
) -> HarnessPreflight:
    """Certify adapters offline and stop before Provider launch when policy is unmet."""
    evaluations: list[dict[str, object]] = []
    for provider, adapter in sorted(adapters.items()):
        probe_method = getattr(adapter, "probe", None)
        certify_method = getattr(adapter, "certify", None)
        legacy_mock = provider == "mock" and (not callable(probe_method) or not callable(certify_method))
        if legacy_mock:
            probe = AdapterProbe(
                provider="mock",
                available=True,
                executable=None,
                provider_version="test-double",
                executable_fingerprint="legacy-mock-test-double",
                help_fingerprint="legacy-mock-test-double",
                adapter_version="legacy-mock/1",
                platform=platform.platform(),
                advertised_capabilities=tuple(capability_map()),
            )
        elif not callable(probe_method) or not callable(certify_method):
            probe = AdapterProbe(
                provider=provider,
                available=True,
                executable=None,
                provider_version=None,
                executable_fingerprint=None,
                help_fingerprint=None,
                adapter_version=str(getattr(adapter, "adapter_version", "legacy-generic/1")),
                platform=platform.platform(),
                diagnostics=("legacy run_stage-only adapter is uncertified",),
            )
        else:
            probe = probe_method()
        current = _current_certification(board, provider, adapter, probe)
        if current is None:
            if legacy_mock:
                report = make_certification_report(
                    probe=probe,
                    capabilities=capability_map(
                        structured_events="verified", tool_events="verified", usage="verified",
                        session_resume="verified", cooperative_cancel="verified", approval_bridge="verified",
                        read_only="verified", patch_output="verified", provider_sandbox="verified",
                    ),
                    trust_tier=TrustTier.MANAGED,
                    live=False,
                    evidence={"checks": ["legacy deterministic mock compatibility"]},
                )
            elif callable(certify_method):
                report = certify_method(live=False)
            else:
                report = make_certification_report(
                    probe=probe,
                    capabilities=capability_map(),
                    trust_tier=TrustTier.OPAQUE,
                    live=False,
                    evidence={"checks": ["legacy adapter compatibility probe"]},
                    failure="legacy run_stage-only adapter is uncertified",
                )
            current = board.record_adapter_certification(report)
            _set_adapter_attr(adapter, "certification_report", report)
        else:
            _set_adapter_attr(adapter, "certification_report", SimpleNamespace(**current))
        trust_tier = str(
            TrustTier.MANAGED
            if legacy_mock
            else getattr(adapter, "trust_tier", current.get("trust_tier") or "opaque")
        )
        capabilities = current.get("capabilities") if isinstance(current.get("capabilities"), Mapping) else {}
        verified = {str(name) for name, state in capabilities.items() if str(state) == "verified"}
        missing = sorted(policy.required_capabilities - verified)
        certification_status = str(current.get("status") or CertificationStatus.UNCERTIFIED)
        managed = trust_tier == str(TrustTier.MANAGED)
        certification_ok = _certification_satisfies(
            certification_status,
            policy.minimum_certification,
            managed=managed,
        )
        if not certification_ok:
            missing.append(f"certification:{policy.minimum_certification}")
        actual_isolation = _actual_isolation(adapter, managed=managed, verified=verified)
        isolation_ok = managed or (
            policy.isolation_requirement == "provider_sandbox" and actual_isolation == "provider_sandbox"
        ) or (
            policy.isolation_requirement == "strong"
            and actual_isolation in {"provider_sandbox", "container"}
            and "cooperative_cancel" in verified
        )
        if not isolation_ok:
            missing.append(f"isolation:{policy.isolation_requirement}")
        _set_adapter_attr(adapter, "isolation_mode", actual_isolation)
        evaluations.append(
            {
                "provider": provider,
                "adapter_version": str(getattr(adapter, "adapter_version", "unknown")),
                "trust_tier": trust_tier,
                "certification_id": current.get("certification_id"),
                "certification_status": certification_status,
                "certification_fingerprint": current.get("fingerprint"),
                "verified_capabilities": sorted(verified),
                "missing_capabilities": sorted(set(missing)),
                "expected_isolation": policy.isolation_requirement,
                "actual_isolation": actual_isolation,
                "next_attempt": _next_attempt(board, run_id, provider),
            }
        )

    subject = {
        "contract_version": "muxdev.isolation_downgrade.v1",
        "run_id": run_id,
        "risk_level": policy.risk_level,
        "risk_tags": sorted(policy.risk_tags),
        "required_capabilities": sorted(policy.required_capabilities),
        "minimum_certification": policy.minimum_certification,
        "expected_isolation": policy.isolation_requirement,
        "providers": evaluations,
    }
    gaps = [item for item in evaluations if item["missing_capabilities"]]
    if not gaps:
        return HarnessPreflight(status="ready", subject=subject)
    if policy.risk_level != "high":
        return HarnessPreflight(
            status="blocked",
            subject=subject,
            reason="ordinary Provider task requires a current offline certification and verified native sandbox",
        )

    subject_hash = canonical_hash(subject)
    existing = board.find_approval(run_id, None, "isolation_downgrade")
    if existing and str(existing.get("subject_hash") or "") == subject_hash:
        if str(existing.get("status")) == str(ApprovalStatus.APPROVED):
            for item in gaps:
                adapter = adapters[str(item["provider"])]
                _set_adapter_attr(adapter, "waiver_approval_id", str(existing["approval_id"]))
            return HarnessPreflight(
                status="ready",
                subject=subject,
                approval_id=str(existing["approval_id"]),
                reason="explicit isolation downgrade waiver",
            )
        if str(existing.get("status")) == str(ApprovalStatus.DENIED):
            return HarnessPreflight(
                status="blocked",
                subject=subject,
                approval_id=str(existing["approval_id"]),
                reason="isolation downgrade denied",
            )
        return HarnessPreflight(
            status="awaiting_approval",
            subject=subject,
            approval_id=str(existing["approval_id"]),
            reason="isolation downgrade requires explicit approval",
        )
    approval_id = board.create_approval(
        run_id,
        None,
        "isolation_downgrade",
        "High-risk task cannot meet the required certified isolation; approve only if possible duplicate or escaped Provider side effects are acceptable.",
        subject=dict(subject),
    )
    return HarnessPreflight(
        status="awaiting_approval",
        subject=subject,
        approval_id=approval_id,
        reason="isolation downgrade requires explicit approval",
    )


def isolation_summary(board: Any, run_id: str) -> dict[str, object]:
    attempts = board.table_rows("provider_attempts", run_id=run_id)
    context = []
    for row in attempts:
        context.append(
            {
                "stage_id": row.get("stage_id"),
                "provider": row.get("provider"),
                "attempt": row.get("attempt"),
                "adapter_version": row.get("adapter_version"),
                "certification_id": row.get("certification_id"),
                "trust_tier": row.get("trust_tier"),
                "isolation_mode": row.get("isolation_mode"),
                "waiver_approval_id": row.get("waiver_approval_id"),
            }
        )
    approvals: list[dict[str, object]] = []
    for row in board.list_approvals(run_id=run_id):
        if str(row.get("type") or "") != "isolation_downgrade":
            continue
        try:
            subject = json.loads(str(row.get("subject_json") or "{}"))
        except json.JSONDecodeError:
            subject = {}
        approvals.append(
            {
                "approval_id": row.get("approval_id"),
                "status": row.get("status"),
                "subject_hash": row.get("subject_hash"),
                "reason": row.get("reason"),
                "risk_level": subject.get("risk_level") if isinstance(subject, dict) else None,
                "risk_tags": subject.get("risk_tags", []) if isinstance(subject, dict) else [],
                "required_capabilities": subject.get("required_capabilities", []) if isinstance(subject, dict) else [],
                "minimum_certification": subject.get("minimum_certification") if isinstance(subject, dict) else None,
                "expected_isolation": subject.get("expected_isolation") if isinstance(subject, dict) else None,
                "provider_gaps": subject.get("providers", []) if isinstance(subject, dict) else [],
            }
        )
    return {
        "run_id": run_id,
        "attempts": context,
        "waivers": approvals,
        "current_requirement": approvals[-1] if approvals else None,
    }


def _current_certification(board: Any, provider: str, adapter: object, probe: object) -> dict[str, object] | None:
    for report in board.list_adapter_certifications(provider=provider):
        if certification_is_current(report, probe):
            return report
    return None


def _certification_satisfies(actual: str, required: str, *, managed: bool) -> bool:
    if managed and actual in {str(CertificationStatus.OFFLINE_VERIFIED), str(CertificationStatus.LIVE_VERIFIED)}:
        return True
    levels = {
        str(CertificationStatus.UNAVAILABLE): 0,
        str(CertificationStatus.UNCERTIFIED): 0,
        str(CertificationStatus.FAILED): 0,
        str(CertificationStatus.STALE): 0,
        str(CertificationStatus.OFFLINE_VERIFIED): 1,
        str(CertificationStatus.LIVE_VERIFIED): 2,
    }
    return levels.get(actual, 0) >= levels.get(required, 1)


def _actual_isolation(adapter: object, *, managed: bool, verified: set[str]) -> str:
    if managed:
        return "managed"
    if "provider_sandbox" in verified:
        return "provider_sandbox"
    return "process"


def _next_attempt(board: Any, run_id: str, provider: str) -> int:
    attempts = [
        int(row.get("attempt") or 0)
        for row in board.table_rows("provider_attempts", run_id=run_id)
        if str(row.get("provider") or "") == provider
    ]
    return (max(attempts) if attempts else 0) + 1


def _set_adapter_attr(adapter: object, name: str, value: object) -> None:
    try:
        setattr(adapter, name, value)
    except (AttributeError, TypeError):
        pass
