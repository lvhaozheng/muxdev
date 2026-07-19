"""Versioned Agent Harness contracts and tamper-evident provider events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Iterator, Mapping, Protocol
from uuid import uuid4

from ..core.redaction import redact
from ..domain import StageExecutionInput, StageExecutionResult

ADAPTER_CONTRACT_VERSION = 1
ADAPTER_POLICY_VERSION = "v0.2-week4"
STANDARD_CAPABILITIES = (
    "structured_events",
    "tool_events",
    "usage",
    "session_resume",
    "cooperative_cancel",
    "approval_bridge",
    "read_only",
    "patch_output",
    "provider_sandbox",
)


class CapabilityVerificationState(StrEnum):
    VERIFIED = "verified"
    ADVERTISED = "advertised"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class CertificationStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    UNCERTIFIED = "uncertified"
    OFFLINE_VERIFIED = "offline_verified"
    LIVE_VERIFIED = "live_verified"
    FAILED = "failed"
    STALE = "stale"


class TrustTier(StrEnum):
    MANAGED = "managed"
    OPAQUE = "opaque"


class IsolationMode(StrEnum):
    NONE = "none"
    PROCESS = "process"
    PROVIDER_SANDBOX = "provider_sandbox"
    CONTAINER = "container"


class HarnessEventSource(StrEnum):
    PROVIDER = "provider"
    HARNESS = "harness"
    DERIVED = "derived"


@dataclass(frozen=True)
class AdapterProbe:
    provider: str
    available: bool
    executable: str | None
    provider_version: str | None
    executable_fingerprint: str | None
    help_fingerprint: str | None
    adapter_version: str
    platform: str
    advertised_capabilities: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CertificationReport:
    certification_id: str
    provider: str
    status: CertificationStatus
    mode: str
    adapter_version: str
    provider_version: str | None
    fingerprint: str
    policy_version: str
    platform: str
    capabilities: Mapping[str, CapabilityVerificationState]
    trust_tier: TrustTier
    issued_at: str
    expires_at: str | None = None
    evidence: Mapping[str, object] = field(default_factory=dict)
    failure: str | None = None

    @property
    def verified_capabilities(self) -> frozenset[str]:
        return frozenset(name for name, value in self.capabilities.items() if str(value) == "verified")

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = str(self.status)
        payload["trust_tier"] = str(self.trust_tier)
        payload["capabilities"] = {name: str(value) for name, value in self.capabilities.items()}
        return payload


@dataclass
class AttemptHandle:
    attempt_id: str
    provider: str
    stage_id: str
    worktree: Path
    session_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CancelResult:
    status: str
    cooperative: bool
    forced: bool = False
    detail: str = ""


@dataclass(frozen=True)
class Unsupported:
    reason: str


@dataclass(frozen=True)
class HarnessEvent:
    event_id: str
    run_id: str
    stage_id: str
    provider: str
    attempt: int
    sequence: int
    event_type: str
    event_version: int
    source: HarnessEventSource
    idempotency_key: str
    payload: Mapping[str, object]
    created_at: str
    prev_hash: str | None
    event_hash: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        stage_id: str,
        provider: str,
        attempt: int,
        sequence: int,
        event_type: str,
        source: HarnessEventSource,
        idempotency_key: str,
        payload: Mapping[str, object] | None = None,
        prev_hash: str | None = None,
        created_at: str | None = None,
        event_id: str | None = None,
    ) -> "HarnessEvent":
        safe_payload = _redact_payload(payload or {})
        timestamp = created_at or datetime.now(UTC).isoformat()
        identifier = event_id or f"hev_{uuid4().hex}"
        fields = {
            "event_id": identifier,
            "run_id": run_id,
            "stage_id": stage_id,
            "provider": provider,
            "attempt": int(attempt),
            "sequence": int(sequence),
            "event_type": event_type,
            "event_version": 1,
            "source": str(source),
            "idempotency_key": idempotency_key,
            "payload": safe_payload,
            "created_at": timestamp,
            "prev_hash": prev_hash,
        }
        return cls(**fields, event_hash=_canonical_hash(fields))

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "HarnessEvent":
        payload = row.get("payload_json")
        return cls(
            event_id=str(row["event_id"]),
            run_id=str(row["run_id"]),
            stage_id=str(row["stage_id"]),
            provider=str(row["provider"]),
            attempt=int(row["attempt"]),
            sequence=int(row["sequence"]),
            event_type=str(row["event_type"]),
            event_version=int(row["event_version"]),
            source=HarnessEventSource(str(row["source"])),
            idempotency_key=str(row["idempotency_key"]),
            payload=json.loads(str(payload or "{}")),
            created_at=str(row["created_at"]),
            prev_hash=str(row["prev_hash"]) if row.get("prev_hash") else None,
            event_hash=str(row["event_hash"]),
        )

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["source"] = str(self.source)
        return payload

    def verify_hash(self) -> bool:
        fields = self.to_dict()
        event_hash = str(fields.pop("event_hash"))
        return event_hash == _canonical_hash(fields)


class AgentHarnessAdapter(Protocol):
    id: str
    adapter_version: str
    trust_tier: TrustTier

    def execute(self, input: StageExecutionInput) -> StageExecutionResult: ...

    def probe(self) -> AdapterProbe: ...

    def certify(self, *, live: bool = False, acknowledged: bool = False, max_cost_usd: float | None = None) -> CertificationReport: ...

    def start(self, **kwargs: object) -> AttemptHandle: ...

    def events(self, handle: AttemptHandle) -> Iterator[HarnessEvent]: ...

    def cancel(self, handle: AttemptHandle) -> CancelResult: ...

    def resume(self, handle: AttemptHandle) -> AttemptHandle | Unsupported: ...


def capability_map(**states: str | CapabilityVerificationState) -> dict[str, CapabilityVerificationState]:
    result = {name: CapabilityVerificationState.UNKNOWN for name in STANDARD_CAPABILITIES}
    for name, value in states.items():
        if name not in result:
            raise ValueError(f"unknown harness capability: {name}")
        result[name] = value if isinstance(value, CapabilityVerificationState) else CapabilityVerificationState(value)
    return result


def _canonical_hash(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _redact_payload(payload: Mapping[str, object]) -> dict[str, object]:
    def visit(value: object) -> object:
        if isinstance(value, Mapping):
            return {str(key): visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return [visit(item) for item in value]
        if isinstance(value, str):
            return redact(value)
        return value

    return dict(visit(payload))
