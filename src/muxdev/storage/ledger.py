"""Hash-chained event ledger for muxdev runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import utc_now
from .contracts import canonical_hash, sha256_file


LEDGER_FILE = "ledger.jsonl"


def append_ledger_event(
    run_dir: Path,
    *,
    run_id: str,
    event_type: str,
    stage_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = run_dir / LEDGER_FILE
    previous = _last_event(path)
    record = {
        "contract_version": "muxdev.ledger_event.v1",
        "run_id": run_id,
        "sequence": int(previous.get("sequence", -1)) + 1 if previous else 0,
        "event_type": event_type,
        "stage_id": stage_id,
        "payload": payload or {},
        "prev_hash": previous.get("event_hash") if previous else None,
        "created_at": utc_now(),
    }
    record["event_hash"] = canonical_hash(record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def verify_ledger(run_dir: Path) -> dict[str, Any]:
    path = run_dir / LEDGER_FILE
    events = _events(path)
    errors: list[str] = []
    previous_hash: str | None = None
    for expected_sequence, event in enumerate(events):
        recorded_hash = event.get("event_hash")
        event_without_hash = {key: value for key, value in event.items() if key != "event_hash"}
        computed_hash = canonical_hash(event_without_hash)
        if event.get("sequence") != expected_sequence:
            errors.append(f"sequence mismatch at {expected_sequence}")
        if event.get("prev_hash") != previous_hash:
            errors.append(f"prev_hash mismatch at sequence {expected_sequence}")
        if recorded_hash != computed_hash:
            errors.append(f"event_hash mismatch at sequence {expected_sequence}")
        previous_hash = str(recorded_hash) if recorded_hash else None
    return {
        "path": str(path),
        "exists": path.exists(),
        "events": len(events),
        "valid": not errors and path.exists(),
        "errors": errors,
        "head_hash": previous_hash,
        "ledger_sha256": sha256_file(path) if path.exists() else None,
    }


def _last_event(path: Path) -> dict[str, Any]:
    events = _events(path)
    return events[-1] if events else {}


def _events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
