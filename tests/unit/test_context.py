from __future__ import annotations

from pathlib import Path

from muxdev.runtime import RunEngine
from muxdev.services.context import (
    build_context_pack,
    build_conversation_context_pack,
    retrieve_verified_memory,
)


def test_context_pack_carries_upstream_facts_with_a_hard_budget(workspace: Path) -> None:
    engine = RunEngine(workspace)
    engine.store.create_run(
        run_id="r-context", task="add login endpoint", workflow="change", profile="lite",
        provider="mock", policy_hash="hash", metadata={},
    )
    engine.store.upsert_stage(
        "r-context", "plan", role="plan", provider="mock", status="completed",
        result={"summary": "plan auth", "parsed": {"steps": ["hash password", "issue token"]}},
    )
    pack = build_context_pack(
        workspace, workspace, engine.store, run_id="r-context", task="add login endpoint", max_chars=600
    )
    assert len(pack.text) <= 600
    assert "plan auth" in pack.text and pack.manifest["upstream_stage_ids"] == ["plan"]
    assert str(pack.manifest["context_digest"]).startswith("sha256:")
    engine.store.close()


def test_memory_promotes_only_still_verified_pass_runs(workspace: Path) -> None:
    engine = RunEngine(workspace)
    first = engine.run("implement login authentication", provider="mock", profile="lite")
    hits = retrieve_verified_memory(workspace, engine.store, "login authentication")
    assert [hit.run_id for hit in hits] == [first.run_id]
    first.report_path.write_text("tampered", encoding="utf-8")
    assert retrieve_verified_memory(workspace, engine.store, "login authentication") == []
    engine.store.close()


def test_conversation_context_keeps_contract_and_questions_inside_budget(workspace: Path) -> None:
    engine = RunEngine(workspace)
    conversation = engine.store.create_conversation(
        conversation_id="conv-context",
        title="context",
        goal="generate report",
        status="clarifying",
        metadata={},
        mode="direct",
        primary_agent_id="mock",
    )
    contract = engine.store.create_delivery_contract(
        conversation["conversation_id"],
        goal="generate report",
        acceptance_criteria=["report exists"],
        allowed_scope=["docs"],
        workflow="change",
        profile="standard",
        provider="mock",
        max_cost_usd=0.5,
        policy={"delivery_standard": {"schema_version": "muxdev.delivery-standard.v2"}},
    )
    question = engine.store.create_conversation_interaction(
        conversation_id=conversation["conversation_id"],
        kind="clarification",
        requirement_id="format",
        prompt="Which report format?",
    )

    pack = build_conversation_context_pack(
        workspace,
        workspace,
        engine.store,
        conversation_id=conversation["conversation_id"],
        contract=contract,
        task="generate report",
        max_chars=800,
    )

    assert len(pack.text) <= 800
    assert "generate report" in pack.text
    assert "Which report format?" in pack.text
    assert pack.manifest["critical_sections_truncated"] is False
    assert pack.manifest["pending_interaction_ids"] == [question["interaction_id"]]
    engine.store.close()
