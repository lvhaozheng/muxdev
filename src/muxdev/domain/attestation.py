"""Versioned signed-delivery contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..core.canonical import canonical_json_bytes, canonical_sha256


ATTESTATION_CONTRACT = "muxdev.delivery-attestation.v1"
SIGNATURE_CONTRACT = "muxdev.ed25519-signature.v1"
BUNDLE_CONTRACT = "muxdev.attestation-bundle.v1"
CANONICAL_JSON_CONTRACT = "muxdev.canonical-json.v1"


@dataclass(frozen=True)
class DeliveryAttestation:
    payload: Mapping[str, Any]

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.payload)

    @property
    def payload_hash(self) -> str:
        return canonical_sha256(self.payload)


@dataclass(frozen=True)
class AttestationVerification:
    integrity_valid: bool
    evidence_valid: bool
    identity_status: str
    trusted: bool
    valid: bool
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    run_id: str | None = None
    attestation_id: str | None = None
    payload_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "integrity_valid": self.integrity_valid,
            "evidence_valid": self.evidence_valid,
            "identity_status": self.identity_status,
            "trusted": self.trusted,
            "valid": self.valid,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "run_id": self.run_id,
            "attestation_id": self.attestation_id,
            "payload_hash": self.payload_hash,
        }


@dataclass(frozen=True)
class PreparedSignature:
    project_id: str
    key_id: str | None
    public_key_fingerprint: str | None
    signature_status: str
    permission_status: str
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
