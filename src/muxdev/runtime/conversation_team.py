"""Role-pipeline team projections for legacy Conversation details."""

from __future__ import annotations

from typing import Any, Mapping

from ..core.redaction import redact
from ..services.router import ProviderRouter
from ..workflows import execution_waves, load_workflow
from .engine import MAX_PARALLEL_WORKERS


class ConversationTeamMixin:
    def _team_projection(
        self,
        contract: Mapping[str, Any] | None,
        run: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not contract:
            return {
                "mode": "role_pipeline",
                "max_parallel": MAX_PARALLEL_WORKERS,
                "roles": [],
                "workers": [],
                "active_stage_ids": [],
            }
        workflow = load_workflow(str(contract["workflow"]))
        policy = contract.get("policy") if isinstance(contract.get("policy"), dict) else {}
        overrides = dict(policy.get("role_providers") or {})
        route: dict[str, Any] = {}
        run_id = str(run.get("run_id") or "") if run else ""
        if run_id:
            try:
                route_row = ProviderRouter(self.store).explain(run_id)
            except FileNotFoundError:
                route_row = {}
            if isinstance(route_row.get("payload"), dict):
                route = dict(route_row["payload"])
        assignments = (
            route.get("role_assignments")
            if isinstance(route.get("role_assignments"), dict)
            else {}
        )
        stage_rows = (
            {str(item["stage_id"]): item for item in self.store.stages(run_id)}
            if run_id
            else {}
        )
        wave_by_stage = {
            stage_id: wave_index
            for wave_index, wave in enumerate(execution_waves(workflow), start=1)
            for stage_id in wave
        }
        workers = []
        profile = str(contract.get("profile") or "standard")
        for stage in workflow.stages:
            if stage.type == "human_gate":
                continue
            row = stage_rows.get(stage.id, {})
            provider = str(
                row.get("provider")
                or overrides.get(str(stage.role or ""))
                or assignments.get(str(stage.role or ""))
                or (
                    "auto（独立）"
                    if stage.role in {"review", "secure"}
                    and profile in {"standard", "strict"}
                    else contract.get("provider")
                )
                or "auto"
            )
            status = str(row.get("status") or "pending")
            if stage.when == "profile.strict" and profile != "strict" and not row:
                status = "skipped"
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            workers.append(
                {
                    "worker_id": (
                        f"{run_id}:{stage.id}:{int(row.get('attempt') or 1)}"
                        if run_id and row
                        else None
                    ),
                    "stage_id": stage.id,
                    "role": stage.role,
                    "provider": provider,
                    "status": status,
                    "attempt": int(row.get("attempt") or 0),
                    "read_only": bool(stage.read_only),
                    "wave": wave_by_stage.get(stage.id),
                    "summary": redact(
                        str(result.get("summary") or result.get("error") or "")
                    )[:500],
                }
            )
        current_stage = str(run.get("current_stage") or "") if run else ""
        active_stage_ids = (
            current_stage.removeprefix("fanout:").split(",") if current_stage else []
        )
        return {
            "mode": "role_pipeline",
            "max_parallel": MAX_PARALLEL_WORKERS,
            "roles": list(
                dict.fromkeys(
                    str(item["role"]) for item in workers if item.get("role")
                )
            ),
            "workers": workers,
            "active_stage_ids": [item for item in active_stage_ids if item],
        }
