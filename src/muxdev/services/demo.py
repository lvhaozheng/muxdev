"""Registered, privacy-safe live and replay product demonstrations."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..core.canonical import canonical_sha256


DEMO_CONTRACT = "muxdev.demo-scenario.v1"
TRUSTED_DELIVERY_SCENARIO = "trusted-delivery-v1"


def list_demo_scenarios() -> list[dict[str, Any]]:
    scenario = load_demo_scenario(TRUSTED_DELIVERY_SCENARIO)
    return [{key: scenario[key] for key in ("scenario_id", "title", "duration_seconds", "fixture_hash", "simulation")}]


def load_demo_scenario(scenario_id: str) -> dict[str, Any]:
    if scenario_id != TRUSTED_DELIVERY_SCENARIO:
        raise ValueError(f"unknown registered demo scenario: {scenario_id}")
    resource = files("muxdev.demo_fixtures").joinpath("trusted_delivery_v1.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("contract_version") != DEMO_CONTRACT or payload.get("scenario_id") != scenario_id:
        raise ValueError("demo fixture contract mismatch")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps or sum(int(row.get("seconds") or 0) for row in steps if isinstance(row, dict)) > 240:
        raise ValueError("demo fixture exceeds four-minute contract")
    payload["fixture_hash"] = canonical_sha256(payload)
    payload["mode"] = "replay"
    payload["label"] = "SIMULATION / REPLAY"
    payload["side_effects"] = False
    return payload


def start_managed_demo(manager: Any, *, scenario_id: str, workspace: Path) -> dict[str, Any]:
    scenario = load_demo_scenario(scenario_id)
    result = manager.submit_task(
        task="Demonstrate capability-constrained routing, independent review, recovery, and signed delivery using managed simulation adapters.",
        workspace=Path(workspace).resolve(),
        provider="mock",
        workflow="dev-lite",
        gate="auto",
        require_approval=set(),
        max_cost_usd=0.0,
        automation={"intent": "dev", "depth": "simple", "workflow": "dev-lite", "roles": ["requirements", "plan", "code", "test", "review"]},
        routing_policy={
            "mode": "auto",
            "delivery_mode": "simulation",
            "allowed_providers": ["mock", "replay"],
            "reviewer_policy": "required",
            "max_cost_usd": 0.0,
        },
    )
    return {
        "contract_version": DEMO_CONTRACT,
        "scenario_id": scenario_id,
        "mode": "live",
        "label": "SIMULATION / LIVE MANAGED RUNTIME",
        "simulation": True,
        "fixture_hash": scenario["fixture_hash"],
        "run": result,
    }
