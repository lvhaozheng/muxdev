"""Provider action repository facade over Blackboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...models import ProviderActionStatus


@dataclass
class ProviderActionsRepository:
    blackboard: Any

    def list_pending(self, run_id: str | None = None) -> list[dict[str, Any]]:
        return self.blackboard.list_provider_actions(status=str(ProviderActionStatus.PENDING), run_id=run_id)

    def list(self, *, status: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        return self.blackboard.list_provider_actions(status=status, run_id=run_id)

    def mark(self, action_id: str, status: ProviderActionStatus | str) -> dict[str, Any]:
        match: dict[str, Any] | None = None
        for row in self.blackboard.table_rows("provider_actions"):
            if row["action_id"] == action_id:
                match = row
                break
        if match is None:
            raise KeyError(f"provider action not found: {action_id}")
        self.blackboard.update_provider_action_status(action_id, status)
        match["status"] = str(status)
        match["updated"] = True
        return match
