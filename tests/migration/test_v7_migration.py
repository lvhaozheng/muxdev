from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from muxdev.storage import ControlStore, migrate_workspace


FIXTURE = Path(__file__).parents[1] / "fixtures" / "pre_refactor_v7" / "blackboard.sqlite"


def test_v7_import_is_atomic_and_legacy_evidence_is_not_rescored(workspace: Path) -> None:
    source = workspace / ".muxdev" / "runs" / "legacy" / "blackboard.sqlite"
    source.parent.mkdir(parents=True)
    shutil.copy2(FIXTURE, source)
    result = migrate_workspace(workspace)
    assert result["migrated"] is True
    assert Path(str(result["backup"])).is_dir()
    with ControlStore(workspace) as store:
        assert len(store.table_names()) == 31
        events = [event for run in store.list_runs() for event in store.events(str(run["run_id"]))]
        assert any(event["type"] == "legacy_evidence" and event["payload"]["rescored"] is False for event in events)
    assert migrate_workspace(workspace)["migrated"] is False


def test_failed_migration_leaves_no_partial_target(workspace: Path) -> None:
    source = workspace / ".muxdev" / "runs" / "broken" / "blackboard.sqlite"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not sqlite")
    with pytest.raises(Exception):
        migrate_workspace(workspace)
    assert not (workspace / ".muxdev" / "control.sqlite").exists()
