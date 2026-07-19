"""Application-level lifecycle service for event-first state changes."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ..models import ApprovalStatus, ProviderActionStatus, RunStatus, StageStatus


@dataclass
class LifecycleService:
    """Coordinates operational events and their SQLite projections."""

    blackboard: Any

    def transition_run(
        self,
        run_id: str,
        status: RunStatus | str,
        *,
        recovery_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        current = self.blackboard.get_run(run_id)
        if str(current.get("status")) in {"blocked", "completed"} and str(status) not in {str(current.get("status")), "aborted"}:
            if not recovery_reason:
                raise ValueError("reopening a blocked or completed run requires recovery_reason")
        return self.blackboard.set_run_status(
            run_id,
            status,
            recovery_reason=recovery_reason,
            idempotency_key=idempotency_key,
        )

    def transition_stage(
        self,
        run_id: str,
        stage_id: str,
        *,
        role: str | None,
        status: StageStatus | str,
        output_path: str | None = None,
        summary: str | None = None,
    ) -> None:
        self.blackboard.upsert_stage(
            run_id, stage_id, role=role, status=status, output_path=output_path, summary=summary
        )

    def request_approval(self, *args: Any, **kwargs: Any) -> str:
        return str(self.blackboard.create_approval(*args, **kwargs))

    def decide_approval(self, approval_id: str, status: ApprovalStatus) -> None:
        self.blackboard.decide_approval(approval_id, status)

    def request_provider_action(self, **kwargs: Any) -> str:
        return str(self.blackboard.create_provider_action(**kwargs))

    def transition_provider_action(self, action_id: str, status: ProviderActionStatus | str) -> None:
        self.blackboard.update_provider_action_status(action_id, status)

    def respond_provider_action(
        self,
        action_id: str,
        *,
        response: Any,
        status: ProviderActionStatus | str = ProviderActionStatus.HANDLED,
    ) -> None:
        self.blackboard.respond_provider_action(action_id, response=response, status=status)

    def fail_worker(
        self,
        run_id: str,
        *,
        error_type: str,
        message: str,
        stage_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        return self.blackboard.fail_worker(
            run_id,
            error_type=error_type,
            message=message,
            stage_id=stage_id,
            idempotency_key=idempotency_key,
        )

    def complete_run(self, run_id: str) -> Any:
        return self.transition_run(run_id, RunStatus.COMPLETED, idempotency_key="run:completed")

    def commit_stage_result(
        self,
        run_id: str,
        stage_id: str,
        *,
        role: str | None,
        artifact_path: Path,
        artifact_kind: str,
        summary: str,
        tests: Iterable[dict[str, Any]] = (),
        usage: Iterable[dict[str, Any]] = (),
    ) -> None:
        artifact = Path(artifact_path).expanduser().resolve()
        if not artifact.is_file():
            raise FileNotFoundError(f"stage artifact does not exist: {artifact}")
        with self.blackboard.unit_of_work():
            self.blackboard.add_artifact(run_id, stage_id, artifact.name, artifact, artifact_kind)
            for item in tests:
                self.blackboard.add_test_result(
                    run_id,
                    stage_id,
                    bool(item.get("passed")),
                    str(item.get("command") or ""),
                    str(item.get("summary") or ""),
                )
            for item in usage:
                self.blackboard.add_usage(
                    run_id,
                    str(item.get("provider") or "unknown"),
                    int(item.get("tokens") or 0),
                    float(item.get("cost_usd") or 0),
                )
            self.transition_stage(
                run_id,
                stage_id,
                role=role,
                status=StageStatus.COMPLETED,
                output_path=str(artifact),
                summary=summary,
            )
            self.blackboard.add_checkpoint(run_id, stage_id, "stage_completed")

    def publish_stage_artifact(
        self,
        run_id: str,
        stage_id: str,
        *,
        role: str | None,
        target: Path,
        content: str | bytes,
        artifact_kind: str,
        summary: str,
    ) -> Path:
        """Durably publish a file before committing the matching DB projection."""
        final_path = Path(target).expanduser().resolve()
        run_root = Path(self.blackboard.run_dir).expanduser().resolve()
        if not final_path.is_relative_to(run_root):
            raise ValueError(f"stage artifact escapes run directory: {final_path}")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = final_path.with_name(f".{final_path.name}.muxdev-tmp-{uuid4().hex}")
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, final_path)
            try:
                self.commit_stage_result(
                    run_id,
                    stage_id,
                    role=role,
                    artifact_path=final_path,
                    artifact_kind=artifact_kind,
                    summary=summary,
                )
            except Exception:
                _write_orphan_marker(
                    final_path,
                    run_id=run_id,
                    stage_id=stage_id,
                    role=role,
                    kind=artifact_kind,
                    summary=summary,
                )
                raise
            _orphan_marker(final_path).unlink(missing_ok=True)
            return final_path
        finally:
            temporary.unlink(missing_ok=True)

    def replay(self, run_id: str) -> dict[str, Any]:
        return self.blackboard.replay_run(run_id)


def reconcile_orphan_artifacts(blackboard: Any, *, adopt: bool = True) -> list[dict[str, Any]]:
    """Inspect identifiable file/DB crash gaps and optionally adopt valid files."""
    root = Path(blackboard.run_dir).expanduser().resolve()
    results: list[dict[str, Any]] = []
    for marker in root.rglob("*.muxdev-orphan.json") if root.exists() else ():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            artifact = Path(str(payload["path"])).expanduser().resolve()
            valid = artifact.is_relative_to(root) and artifact.is_file() and _sha256(artifact) == payload.get("sha256")
            status = "valid_orphan" if valid else "invalid_orphan"
            if valid and adopt:
                LifecycleService(blackboard).commit_stage_result(
                    str(payload["run_id"]),
                    str(payload["stage_id"]),
                    role=str(payload["role"]) if payload.get("role") else None,
                    artifact_path=artifact,
                    artifact_kind=str(payload.get("kind") or "stage_output"),
                    summary=str(payload.get("summary") or "recovered orphan artifact"),
                )
                marker.unlink(missing_ok=True)
                status = "adopted"
            results.append({"marker": str(marker), "path": str(artifact), "status": status})
        except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
            results.append({"marker": str(marker), "status": "invalid_orphan", "error": type(exc).__name__})
    return results


def _write_orphan_marker(path: Path, **payload: Any) -> None:
    marker = _orphan_marker(path)
    body = {**payload, "path": str(path), "sha256": _sha256(path)}
    marker.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _orphan_marker(path: Path) -> Path:
    return path.with_name(path.name + ".muxdev-orphan.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()
