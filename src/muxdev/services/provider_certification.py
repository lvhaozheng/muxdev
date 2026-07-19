"""Application service for Adapter certification persistence."""

from __future__ import annotations

from typing import Any

from ..daemon.paths import default_daemon_paths
from ..providers import get_runtime_provider
from ..storage import Blackboard


def certify_adapter(
    name: str,
    *,
    live: bool = False,
    acknowledged: bool = False,
    max_cost_usd: float | None = None,
) -> dict[str, Any]:
    adapter = get_runtime_provider(name)
    report = adapter.certify(live=live, acknowledged=acknowledged, max_cost_usd=max_cost_usd)
    paths = default_daemon_paths().ensure()
    with Blackboard(paths.data_dir, db_path=paths.db_path) as board:
        return board.record_adapter_certification(report)


def list_certifications(name: str | None = None) -> list[dict[str, Any]]:
    paths = default_daemon_paths().ensure()
    with Blackboard(paths.data_dir, db_path=paths.db_path) as board:
        return board.list_adapter_certifications(provider=name)
