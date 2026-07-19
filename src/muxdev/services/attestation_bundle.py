"""Deterministic, path-safe ``.muxattest`` export and offline verification."""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from uuid import uuid4

from cryptography.hazmat.primitives import serialization

from ..core.canonical import canonical_sha256, sha256_bytes
from ..domain.attestation import BUNDLE_CONTRACT
from ..storage.contracts import sha256_file
from .attestation import verify_attestation_record


MAX_MEMBERS = 1_024
MAX_MEMBER_SIZE = 64 * 1024 * 1024
MAX_TOTAL_SIZE = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class AttestationBundleError(ValueError):
    """A bundle is malformed, unsafe, or inconsistent with its declaration."""


def export_attestation_bundle(
    board: Any,
    *,
    run_id: str,
    run_dir: Path,
    output: Path,
    record: Mapping[str, Any] | None = None,
    record_export: bool = True,
) -> dict[str, Any]:
    run_dir = Path(run_dir).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    attestation = dict(record or board.latest_delivery_attestation(run_id) or {})
    if not attestation:
        raise FileNotFoundError(f"delivery attestation not found: {run_id}")
    payload = dict(attestation.get("payload") or {})
    signature = attestation.get("signature")
    members: dict[str, bytes] = {
        "attestation/payload.json": _json_bytes(payload),
        "attestation/signature.json": _json_bytes(dict(signature)) if isinstance(signature, Mapping) else _json_bytes({"status": "unsigned"}),
    }
    if isinstance(signature, Mapping) and signature.get("public_key"):
        spki = base64.b64decode(str(signature["public_key"]), validate=True)
        public_key = serialization.load_der_public_key(spki)
        members["attestation/public-key.pem"] = public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    _add_evidence_members(members, run_dir=run_dir, payload=payload)
    included_artifacts = _add_allowlisted_artifacts(members, run_dir=run_dir, payload=payload)
    members["projections/delivery.json"] = _json_bytes(
        {
            "routing": payload.get("routing"), "review": payload.get("review"),
            "approvals": payload.get("approvals"), "provider_attempts": payload.get("provider_attempts"),
            "harness_heads": payload.get("harness_heads"),
            "execution_head_at_signing": payload.get("execution_head_at_signing"),
            "tests": payload.get("tests"), "environment": payload.get("environment"),
        }
    )
    _validate_member_map(members)
    entries = [
        {
            "path": name,
            "size": len(content),
            "sha256": sha256_bytes(content),
            "media_type": _media_type(name),
        }
        for name, content in sorted(members.items())
    ]
    manifest = {
        "contract_version": BUNDLE_CONTRACT,
        "run_id": run_id,
        "attestation_id": attestation.get("attestation_id"),
        "payload_hash": attestation.get("payload_hash") or canonical_sha256(payload),
        "created_at": payload.get("signed_at"),
        "artifact_mappings": included_artifacts,
        "entries": entries,
    }
    members["manifest.json"] = _json_bytes(manifest)
    _validate_member_map(members)

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent))
    os.close(fd)
    temp = Path(raw_temp)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for name in sorted(members):
                info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                bundle.writestr(info, members[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(temp, output)
    finally:
        if temp.exists():
            temp.unlink()
    digest = sha256_file(output)
    export_id = f"atex_{uuid4().hex}"
    if record_export:
        export_record = board.record_attestation_export(
            export_id=export_id,
            run_id=run_id,
            attestation_id=str(attestation["attestation_id"]),
            bundle_path=output,
            bundle_sha256=digest,
            size_bytes=output.stat().st_size,
        )
        export_id = str(export_record["export_id"])
    return {
        "export_id": export_id,
        "run_id": run_id,
        "attestation_id": attestation.get("attestation_id"),
        "bundle": str(output),
        "sha256": digest,
        "size_bytes": output.stat().st_size,
        "member_count": len(members),
    }


def verify_attestation_bundle(
    archive: Path,
    *,
    trusted_fingerprint: str | None = None,
    require_trusted: bool = False,
) -> dict[str, Any]:
    archive = Path(archive).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            infos = bundle.infolist()
            _validate_zip_infos(infos)
            names = {info.filename for info in infos}
            if "manifest.json" not in names:
                raise AttestationBundleError("bundle manifest is missing")
            manifest = _read_json_member(bundle, "manifest.json")
            if manifest.get("contract_version") != BUNDLE_CONTRACT:
                raise AttestationBundleError("unsupported attestation bundle format")
            declared = manifest.get("entries")
            if not isinstance(declared, list):
                raise AttestationBundleError("bundle entries declaration is missing")
            declared_by_name: dict[str, dict[str, Any]] = {}
            for item in declared:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise AttestationBundleError("invalid bundle entry declaration")
                name = str(item["path"])
                if name in declared_by_name:
                    raise AttestationBundleError(f"duplicate declared member: {name}")
                declared_by_name[name] = item
            actual_content = names - {"manifest.json"}
            if actual_content != set(declared_by_name):
                missing = sorted(set(declared_by_name) - actual_content)
                undeclared = sorted(actual_content - set(declared_by_name))
                if missing:
                    errors.append("missing declared members: " + ", ".join(missing))
                if undeclared:
                    errors.append("undeclared members: " + ", ".join(undeclared))
            for name, item in declared_by_name.items():
                if name not in names:
                    continue
                raw = _read_limited(bundle, name)
                if len(raw) != int(item.get("size") or -1):
                    errors.append(f"member size mismatch: {name}")
                if sha256_bytes(raw) != item.get("sha256"):
                    errors.append(f"member hash mismatch: {name}")
            payload = _read_json_member(bundle, "attestation/payload.json")
            signature_doc = _read_json_member(bundle, "attestation/signature.json")
            signature: Mapping[str, Any] | None = signature_doc if signature_doc.get("signature") else None
            evidence_valid, evidence_errors = _verify_offline_evidence(bundle, payload)
            errors.extend(evidence_errors)
            signed = verify_attestation_record(
                payload,
                signature,
                evidence_valid=evidence_valid,
                trusted_fingerprint=trusted_fingerprint,
                require_trusted=require_trusted,
            )
            errors.extend(str(item) for item in signed.get("errors", []))
            warnings.extend(str(item) for item in signed.get("warnings", []))
            if manifest.get("payload_hash") != canonical_sha256(payload):
                errors.append("manifest payload hash mismatch")
            errors.extend(_verify_artifact_mappings(bundle, manifest, payload))
            projection = _read_json_member(bundle, "projections/delivery.json")
            expected_projection = {
                "routing": payload.get("routing"), "review": payload.get("review"),
                "approvals": payload.get("approvals"), "provider_attempts": payload.get("provider_attempts"),
                "harness_heads": payload.get("harness_heads"),
                "execution_head_at_signing": payload.get("execution_head_at_signing"),
                "tests": payload.get("tests"), "environment": payload.get("environment"),
            }
            if canonical_sha256(projection) != canonical_sha256(expected_projection):
                errors.append("delivery projection does not match signed payload")
            if signature and "attestation/public-key.pem" in names:
                pem_key = serialization.load_pem_public_key(_read_limited(bundle, "attestation/public-key.pem"))
                pem_spki = pem_key.public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                )
                if base64.b64encode(pem_spki).decode("ascii") != signature.get("public_key"):
                    errors.append("public PEM does not match signature envelope")
            result = {
                **signed,
                "valid": bool(signed.get("valid")) and not errors,
                "evidence_valid": evidence_valid,
                "errors": list(dict.fromkeys(errors)),
                "warnings": list(dict.fromkeys(warnings)),
                "bundle_format": manifest.get("contract_version"),
                "bundle_sha256": sha256_file(archive),
                "member_count": len(infos),
                "offline": True,
            }
            return result
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return {
            "integrity_valid": False, "evidence_valid": False,
            "identity_status": "mismatch", "trusted": False, "valid": False,
            "warnings": warnings, "errors": [str(exc)], "offline": True,
        }


def _add_evidence_members(members: dict[str, bytes], *, run_dir: Path, payload: Mapping[str, Any]) -> None:
    evidence_dir = run_dir / "evidence"
    events_path = evidence_dir / "events.jsonl"
    if events_path.is_file():
        members["evidence/events.jsonl"] = events_path.read_bytes()
    manifest_path = evidence_dir / "manifest.json"
    if manifest_path.is_file():
        members["evidence/manifest.json"] = manifest_path.read_bytes()
    evaluation_path = evidence_dir / "evaluation.json"
    if evaluation_path.is_file():
        members["evidence/evaluation.json"] = evaluation_path.read_bytes()


def _add_allowlisted_artifacts(
    members: dict[str, bytes], *, run_dir: Path, payload: Mapping[str, Any]
) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
    for item in artifacts:
        if not isinstance(item, Mapping):
            continue
        relative_text = str(item.get("relative_path") or "")
        relative = PurePosixPath(relative_text)
        if not _safe_member_name(relative_text) or relative_text not in {
            "diff.patch", "attestation/final_report.sanitized.md",
            "contracts/delivery_contract.json", "validation/blind_validator_panel.json",
            "deliverables/test_report.md", "deliverables/review_report.md",
        }:
            continue
        source = (run_dir / Path(*relative.parts)).resolve()
        if not source.is_relative_to(run_dir) or not source.is_file() or source.is_symlink():
            continue
        raw = source.read_bytes()
        digest = sha256_bytes(raw)
        if digest != item.get("sha256"):
            raise AttestationBundleError(f"allowlisted artifact changed after signing: {relative_text}")
        bundle_path = f"artifacts/{digest.removeprefix('sha256:')}/{source.name}"
        members[bundle_path] = raw
        mappings.append(
            {"source_relative_path": relative_text, "bundle_path": bundle_path, "sha256": digest, "size": len(raw)}
        )
    return sorted(mappings, key=lambda item: str(item["source_relative_path"]))


def _verify_offline_evidence(bundle: zipfile.ZipFile, payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    names = set(bundle.namelist())
    required = {"evidence/events.jsonl", "evidence/manifest.json", "evidence/evaluation.json"}
    if not required <= names:
        return False, ["offline Evidence v2 files are incomplete"]
    events_raw = _read_limited(bundle, "evidence/events.jsonl")
    manifest_raw = _read_limited(bundle, "evidence/manifest.json")
    evaluation_raw = _read_limited(bundle, "evidence/evaluation.json")
    evidence_binding = payload.get("evidence") if isinstance(payload.get("evidence"), Mapping) else {}
    if sha256_bytes(events_raw) != evidence_binding.get("events_hash"):
        errors.append("Evidence events hash mismatch")
    if sha256_bytes(manifest_raw) != evidence_binding.get("manifest_hash"):
        errors.append("Evidence manifest hash mismatch")
    if sha256_bytes(evaluation_raw) != evidence_binding.get("evaluation_hash"):
        errors.append("Evidence evaluation hash mismatch")
    manifest = json.loads(manifest_raw)
    events: list[dict[str, Any]] = []
    for index, line in enumerate(events_raw.decode("utf-8").splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"invalid Evidence event line: {index}")
            continue
        if isinstance(value, dict):
            events.append(value)
    prev_hash: str | None = None
    for event in events:
        event_hash = event.get("event_hash")
        unsigned = dict(event)
        unsigned.pop("event_hash", None)
        if event.get("prev_hash") != prev_hash:
            errors.append(f"Evidence prev_hash mismatch: {event.get('id')}")
        if canonical_sha256(unsigned) != event_hash:
            errors.append(f"Evidence event hash mismatch: {event.get('id')}")
        prev_hash = str(event_hash) if event_hash else None
    if int(manifest.get("event_count") or 0) != len(events):
        errors.append("Evidence manifest event_count mismatch")
    if manifest.get("head_hash") != prev_hash or evidence_binding.get("head_hash") != prev_hash:
        errors.append("Evidence chain head mismatch")
    return not errors, errors


def _verify_artifact_mappings(
    bundle: zipfile.ZipFile,
    manifest: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    raw_mappings = manifest.get("artifact_mappings")
    mappings = raw_mappings if isinstance(raw_mappings, list) else []
    by_source = {
        str(item.get("source_relative_path")): item
        for item in mappings
        if isinstance(item, Mapping) and item.get("source_relative_path")
    }
    signed_artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
    for artifact in signed_artifacts:
        if not isinstance(artifact, Mapping):
            continue
        source = str(artifact.get("relative_path") or "")
        mapping = by_source.get(source)
        if not mapping:
            errors.append(f"signed artifact is missing from bundle: {source}")
            continue
        bundle_path = str(mapping.get("bundle_path") or "")
        if bundle_path not in bundle.namelist():
            errors.append(f"mapped artifact member is missing: {source}")
            continue
        raw = _read_limited(bundle, bundle_path)
        digest = sha256_bytes(raw)
        if digest != artifact.get("sha256") or digest != mapping.get("sha256"):
            errors.append(f"signed artifact hash mismatch: {source}")
        if len(raw) != int(artifact.get("size") or -1) or len(raw) != int(mapping.get("size") or -1):
            errors.append(f"signed artifact size mismatch: {source}")
    if set(by_source) != {
        str(item.get("relative_path") or "") for item in signed_artifacts if isinstance(item, Mapping)
    }:
        errors.append("bundle artifact mapping contains undeclared source")
    return errors


def _validate_member_map(members: Mapping[str, bytes]) -> None:
    if len(members) > MAX_MEMBERS:
        raise AttestationBundleError("bundle contains too many members")
    total = 0
    for name, content in members.items():
        if not _safe_member_name(name):
            raise AttestationBundleError(f"unsafe bundle member: {name!r}")
        if len(content) > MAX_MEMBER_SIZE:
            raise AttestationBundleError(f"bundle member is too large: {name}")
        total += len(content)
    if total > MAX_TOTAL_SIZE:
        raise AttestationBundleError("bundle uncompressed size exceeds limit")


def _validate_zip_infos(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_MEMBERS:
        raise AttestationBundleError("bundle contains too many members")
    seen: set[str] = set()
    total = 0
    for info in infos:
        name = info.filename
        if not _safe_member_name(name) or name in seen:
            raise AttestationBundleError(f"unsafe or duplicate bundle member: {name!r}")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise AttestationBundleError(f"symbolic links are forbidden: {name}")
        if info.file_size > MAX_MEMBER_SIZE:
            raise AttestationBundleError(f"bundle member is too large: {name}")
        if info.file_size and info.compress_size == 0:
            raise AttestationBundleError(f"invalid compressed size: {name}")
        if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise AttestationBundleError(f"suspicious compression ratio: {name}")
        total += info.file_size
        seen.add(name)
    if total > MAX_TOTAL_SIZE:
        raise AttestationBundleError("bundle uncompressed size exceeds limit")


def _safe_member_name(name: str) -> bool:
    if not name or "\\" in name or re.match(r"^[A-Za-z]:", name):
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and all(part not in {"", "."} for part in path.parts)


def _read_limited(bundle: zipfile.ZipFile, name: str) -> bytes:
    info = bundle.getinfo(name)
    if info.file_size > MAX_MEMBER_SIZE:
        raise AttestationBundleError(f"bundle member is too large: {name}")
    with bundle.open(info, "r") as handle:
        raw = handle.read(MAX_MEMBER_SIZE + 1)
    if len(raw) > MAX_MEMBER_SIZE:
        raise AttestationBundleError(f"bundle member exceeds streaming limit: {name}")
    return raw


def _read_json_member(bundle: zipfile.ZipFile, name: str) -> dict[str, Any]:
    raw = _read_limited(bundle, name)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise AttestationBundleError(f"JSON member must contain an object: {name}")
    return payload


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _media_type(name: str) -> str:
    suffix = PurePosixPath(name).suffix.lower()
    return {
        ".json": "application/json", ".jsonl": "application/x-ndjson", ".pem": "application/x-pem-file",
        ".md": "text/markdown", ".patch": "text/x-diff", ".txt": "text/plain",
    }.get(suffix, "application/octet-stream")
