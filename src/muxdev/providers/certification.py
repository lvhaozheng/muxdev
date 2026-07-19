"""Offline-first certification for Agent Harness adapters."""

from __future__ import annotations

import hashlib
import json
import platform
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping
from uuid import uuid4

from .harness import (
    ADAPTER_POLICY_VERSION,
    AdapterProbe,
    CertificationReport,
    CertificationStatus,
    TrustTier,
    capability_map,
)

LIVE_CERTIFICATION_DAYS = 30


def make_certification_report(
    *,
    probe: AdapterProbe,
    capabilities: Mapping[str, object],
    trust_tier: TrustTier,
    live: bool,
    evidence: Mapping[str, object],
    failure: str | None = None,
    now: datetime | None = None,
) -> CertificationReport:
    issued = now or datetime.now(UTC)
    status = CertificationStatus.FAILED if failure else (
        CertificationStatus.LIVE_VERIFIED if live else CertificationStatus.OFFLINE_VERIFIED
    )
    if not probe.available:
        status = CertificationStatus.UNAVAILABLE
    normalized = capability_map(**{name: str(value) for name, value in capabilities.items()})
    fingerprint_payload = {
        "provider": probe.provider,
        "provider_version": probe.provider_version,
        "executable_fingerprint": probe.executable_fingerprint,
        "help_fingerprint": probe.help_fingerprint,
        "adapter_version": probe.adapter_version,
        "platform": probe.platform,
        "policy_version": ADAPTER_POLICY_VERSION,
        "mode": "live" if live else "offline",
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return CertificationReport(
        certification_id=f"cert_{uuid4().hex}",
        provider=probe.provider,
        status=status,
        mode="live" if live else "offline",
        adapter_version=probe.adapter_version,
        provider_version=probe.provider_version,
        fingerprint=fingerprint,
        policy_version=ADAPTER_POLICY_VERSION,
        platform=probe.platform or platform.platform(),
        capabilities=normalized,
        trust_tier=trust_tier,
        issued_at=issued.isoformat(),
        expires_at=(issued + timedelta(days=LIVE_CERTIFICATION_DAYS)).isoformat() if live and not failure else None,
        evidence=_safe_evidence(evidence),
        failure=failure,
    )


def certification_is_current(report: Mapping[str, object], probe: AdapterProbe, *, now: datetime | None = None) -> bool:
    if str(report.get("status")) not in {
        str(CertificationStatus.OFFLINE_VERIFIED), str(CertificationStatus.LIVE_VERIFIED)
    }:
        return False
    current_payload = {
        "provider": probe.provider,
        "provider_version": probe.provider_version,
        "executable_fingerprint": probe.executable_fingerprint,
        "help_fingerprint": probe.help_fingerprint,
        "adapter_version": probe.adapter_version,
        "platform": probe.platform,
        "policy_version": ADAPTER_POLICY_VERSION,
        "mode": str(report.get("mode") or "offline"),
    }
    current = hashlib.sha256(json.dumps(current_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if current != str(report.get("fingerprint") or ""):
        return False
    expires_at = report.get("expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(str(expires_at))
            return expiry > (now or datetime.now(UTC))
        except ValueError:
            return False
    return True


def require_live_acknowledgement(*, live: bool, acknowledged: bool, max_cost_usd: float | None) -> None:
    if not live:
        return
    if not acknowledged:
        raise ValueError("live certification requires explicit --yes acknowledgement")
    if max_cost_usd is None or max_cost_usd <= 0:
        raise ValueError("live certification requires a positive --max-cost-usd budget")


def _safe_evidence(evidence: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "checks",
        "fixture_hashes",
        "test_id",
        "summary",
        "usage",
        "exit_code",
        "sandbox_sentinel",
    }
    return {str(key): value for key, value in evidence.items() if key in allowed}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(parts: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()
