"""Content-addressed cache for reproducible local automation artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config.loader import path_config
from ..storage.contracts import canonical_hash, sha256_file


class CasCache:
    """Small CAS rooted under `.muxdev/cache/cas`."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.root = path_config(workspace, "runtime_root") / "cache" / "cas"
        self.root.mkdir(parents=True, exist_ok=True)

    def key_for(self, *, kind: str, inputs: dict[str, Any]) -> str:
        return canonical_hash({"kind": kind, "inputs": inputs})

    def put_json(self, *, kind: str, payload: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        key = self.key_for(kind=kind, inputs={"payload": payload, "metadata": metadata or {}})
        short = key.split(":", 1)[-1]
        path = self.root / kind / f"{short}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {
            "cache_key": key,
            "kind": kind,
            "path": str(path),
            "value_hash": sha256_file(path),
            "metadata": metadata or {},
        }

    def get(self, cache_key: str) -> dict[str, Any] | None:
        short = cache_key.split(":", 1)[-1]
        for path in self.root.rglob(f"{short}.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            return {"cache_key": cache_key, "path": str(path), "payload": payload, "value_hash": sha256_file(path)}
        return None
