"""Minimal in-toto Statement and DSSE Envelope for Evidence v3 reports."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat


PAYLOAD_TYPE = "application/vnd.in-toto+json"
PREDICATE_TYPE = "https://muxdev.dev/attestation/evidence-report/v3"


def generate_signing_key(path: Path) -> Path:
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return path


def export_dsse(report_path: Path, *, private_key: Path | None = None, output: Path | None = None) -> dict[str, object]:
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    subject = report.get("subject") if isinstance(report.get("subject"), dict) else {}
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": str(subject.get("name") or report.get("run_id")), "digest": _digest_map(str(subject.get("digest") or ""))}],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "evidenceReport": {
                "name": report_path.name,
                "sha256": hashlib.sha256(report_bytes).hexdigest(),
            },
            "policyHash": ((report.get("policy") or {}).get("policy_hash")),
            "gateStatus": ((report.get("decision") or {}).get("status")),
        },
    }
    payload = json.dumps(statement, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    signatures: list[dict[str, str]] = []
    if private_key:
        key = _load_private_key(private_key)
        signature = key.sign(_pae(PAYLOAD_TYPE, payload))
        public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        signatures.append({"keyid": hashlib.sha256(public).hexdigest(), "sig": base64.b64encode(signature).decode()})
    envelope = {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode(),
        "signatures": signatures,
    }
    target = output or report_path.with_name("attestation.dsse.json")
    target.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(target), "signed": bool(signatures), "envelope": envelope}


def verify_dsse(path: Path, *, public_key: Path | None = None, report_path: Path | None = None) -> dict[str, object]:
    errors: list[str] = []
    envelope = json.loads(path.read_text(encoding="utf-8"))
    payload_type = str(envelope.get("payloadType") or "")
    try:
        payload = base64.b64decode(str(envelope.get("payload") or ""), validate=True)
        statement = json.loads(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"valid": False, "errors": [f"invalid DSSE payload: {exc}"]}
    if payload_type != PAYLOAD_TYPE:
        errors.append("unexpected DSSE payload type")
    if report_path:
        expected = hashlib.sha256(report_path.read_bytes()).hexdigest()
        actual = str((((statement.get("predicate") or {}).get("evidenceReport") or {}).get("sha256") or ""))
        if expected != actual:
            errors.append("evidence report digest does not match the attestation")
    signatures = envelope.get("signatures") if isinstance(envelope.get("signatures"), list) else []
    if public_key:
        if not signatures:
            errors.append("signature is required but missing")
        else:
            try:
                _load_public_key(public_key).verify(
                    base64.b64decode(str(signatures[0].get("sig") or "")), _pae(payload_type, payload)
                )
            except (InvalidSignature, ValueError, TypeError):
                errors.append("DSSE signature verification failed")
    return {"valid": not errors, "errors": errors, "signed": bool(signatures), "statement": statement}


def _pae(payload_type: str, payload: bytes) -> bytes:
    encoded_type = payload_type.encode()
    return b"DSSEv1 " + str(len(encoded_type)).encode() + b" " + encoded_type + b" " + str(len(payload)).encode() + b" " + payload


def _digest_map(digest: str) -> dict[str, str]:
    algorithm, _, value = digest.partition(":")
    return {algorithm or "sha256": value or digest}


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("only Ed25519 private keys are supported")
    return key


def _load_public_key(path: Path) -> Ed25519PublicKey:
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    key = load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("only Ed25519 public keys are supported")
    return key
