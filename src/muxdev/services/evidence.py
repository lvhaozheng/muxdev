"""Evidence verification helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..storage import Blackboard, sha256_file, verify_ledger


def verify_run_evidence(run_dir: Path, run_id: str, blackboard: Blackboard | None = None) -> dict[str, Any]:
    owns_blackboard = blackboard is None
    board = blackboard or Blackboard(run_dir)
    try:
        ledger = verify_ledger(run_dir)
        contract_errors = _verify_rows(board.table_rows("stage_contracts", run_id=run_id), "path", "contract_hash")
        evidence_errors = _verify_rows(board.table_rows("evidence_bundles", run_id=run_id), "path", "bundle_hash")
        validator_errors = _verify_rows(board.table_rows("validator_panels", run_id=run_id), "path", "validator_hash")
        errors = [*ledger["errors"], *contract_errors, *evidence_errors, *validator_errors]
        return {
            "run_id": run_id,
            "valid": bool(ledger["valid"]) and not errors,
            "ledger": ledger,
            "contracts": len(board.table_rows("stage_contracts", run_id=run_id)),
            "evidence_bundles": len(board.table_rows("evidence_bundles", run_id=run_id)),
            "validators": len(board.table_rows("validator_panels", run_id=run_id)),
            "errors": errors,
        }
    finally:
        if owns_blackboard:
            board.close()


def _verify_rows(rows: list[dict[str, Any]], path_key: str, hash_key: str) -> list[str]:
    errors: list[str] = []
    for row in rows:
        path = Path(str(row.get(path_key) or ""))
        expected = row.get(hash_key)
        if not path.exists():
            errors.append(f"missing artifact: {path}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            errors.append(f"hash mismatch: {path}")
    return errors
