from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_simplification_targets() -> None:
    subprocess.run(["python", "scripts/architecture_metrics.py", "--output", "release-artifacts/architecture-current.json"], cwd=ROOT, check=True)
    metrics = json.loads((ROOT / "release-artifacts" / "architecture-current.json").read_text(encoding="utf-8"))
    source, surfaces = metrics["source"], metrics["surfaces"]
    assert source["logical_lines"] <= 27_253
    assert source["python_files"] <= 124
    assert source["functions_over_120_lines"] == []
    assert max(item["lines"] for item in source["largest_files"]) <= 1_000
    assert metrics["architecture"]["layer_cycles"] == []
    assert metrics["architecture"]["four_layers"]["reverse_dependency_violations"] == []
    assert surfaces == {
        "http_routes": 45, "typer_commands": 40, "sqlite_tables": 22, "workflows": 4,
        "workflow_names": ["change", "design", "review", "test"], "builtin_skills": 5,
        "builtin_skill_names": ["default-code", "default-plan", "default-review", "default-secure", "default-test"],
        "delivery_standard_sections": 0,
    }
