"""Small helpers for deterministic worker identities and team assignment."""

from __future__ import annotations

from typing import Mapping

from ..workflows import execution_waves


class TeamRuntimeMixin:
    @staticmethod
    def _worker_id(run_id: str, stage_id: str, attempt: int) -> str:
        return f"{run_id}:{stage_id}:{attempt}"

    def _team_payload(self, workflow, route, role_providers):
        wave_by_stage = {
            stage_id: wave_index
            for wave_index, wave in enumerate(execution_waves(workflow), start=1)
            for stage_id in wave
        }
        members = [
            {
                "stage_id": stage.id,
                "role": stage.role,
                "provider": self._stage_provider(stage.role, route, role_providers),
                "read_only": bool(stage.read_only),
                "wave": wave_by_stage.get(stage.id),
            }
            for stage in workflow.stages
            if stage.type != "human_gate"
        ]
        return {"mode": "role_pipeline", "max_parallel": 4, "members": members}

    @staticmethod
    def _stage_provider(
        role: str | None,
        route: Mapping[str, object],
        overrides: Mapping[str, str],
    ) -> str:
        assignments = (
            route.get("role_assignments")
            if isinstance(route.get("role_assignments"), Mapping) else {}
        )
        if role and role in assignments:
            return str(assignments[role])
        if role and role in overrides:
            return overrides[role]
        if role in {"review", "secure"} and route.get("reviewer_provider"):
            return str(route["reviewer_provider"])
        return str(route.get("main_provider") or "")


__all__ = ["TeamRuntimeMixin"]
