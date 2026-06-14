from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from muxdev.storage.trace import read_recent_trace


def test_read_recent_trace_returns_tail_in_order() -> None:
    workspace = Path(".test_workspaces") / f"trace_{uuid.uuid4().hex}"
    run_dir = workspace / "run"
    try:
        run_dir.mkdir(parents=True)
        trace = run_dir / "trace.jsonl"
        trace.write_text(
            "\n".join(
                json.dumps({"time": f"t{i}", "type": "event", "data": {"index": i}})
                for i in range(100)
            )
            + "\n",
            encoding="utf-8",
        )

        events = read_recent_trace(run_dir, limit=5)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert [event["data"]["index"] for event in events] == [95, 96, 97, 98, 99]
