from __future__ import annotations

import hashlib
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from muxdev.models.evidence import EvidencePolicy
from muxdev.runtime.collaboration_delivery import (
    _extend_skill_policy,
    _record_skill_evidence,
)
from muxdev.services.gate import evaluate_gate
from muxdev.services.skills.catalog import (
    SkillCatalogError,
    audit_skill_source,
    build_skill_catalog,
    find_catalog_skill,
    read_catalog_skill_file,
)
from muxdev.services.skills.product import (
    bind_product_skill,
    freeze_bound_skills,
    load_product_skill,
)
from muxdev.storage import ControlStore
from muxdev.workbench import WorkbenchStore


def _write_skill(root: Path, name: str, body: str = "frozen instructions") -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: Test {name}\n"
            "version: 1.0.0\n"
            "---\n\n"
            f"# {name}\n\n{body}\n"
        ),
        encoding="utf-8",
    )
    return directory


def test_catalog_disambiguates_native_source_collisions(workspace: Path) -> None:
    _write_skill(workspace / ".agents" / "skills", "collision")
    _write_skill(workspace / ".codex" / "skills", "collision")
    _write_skill(workspace / ".deepcode" / "skills", "collision")

    matches = [
        item
        for item in build_skill_catalog(workspace)
        if item["name"] == "collision"
    ]

    assert {item["qualified_name"] for item in matches} == {
        "collision@project-agents",
        "collision@project-codex",
        "collision@project-deepcode",
    }
    codex = next(item for item in matches if item["source_id"] == "project-codex")
    assert codex["consumer_compatibility"]["codex"] == "native"
    shared = next(item for item in matches if item["source_id"] == "project-agents")
    deepcode = next(item for item in matches if item["source_id"] == "project-deepcode")
    assert shared["consumer_compatibility"]["deepcode"] == "native"
    assert deepcode["consumer_compatibility"]["deepcode"] == "native"


def test_source_audit_rejects_symlink_escape(workspace: Path) -> None:
    source = workspace / "external-skills"
    skill = _write_skill(source, "escape")
    outside = workspace / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = skill / "linked.md"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")

    with pytest.raises(SkillCatalogError, match="escapes"):
        audit_skill_source(source)


def test_skill_file_load_has_independent_256kib_limit(workspace: Path) -> None:
    skill = _write_skill(workspace / ".agents" / "skills", "large-file")
    (skill / "reference.md").write_text("x" * (256 * 1024 + 1), encoding="utf-8")

    catalog = build_skill_catalog(workspace)
    item = find_catalog_skill(catalog, "large-file")

    with pytest.raises(SkillCatalogError, match="256 KiB"):
        read_catalog_skill_file(item, "reference.md")


def test_frozen_skill_load_is_audited_and_current_generation_gates_delivery(
    workspace: Path,
) -> None:
    skill_dir = _write_skill(
        workspace / ".agents" / "skills",
        "trusted-delivery-test",
        "original frozen instructions",
    )
    raw_token = "unit-control-token"
    token_hash = "sha256:" + hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    workbench = WorkbenchStore(workspace / ".test-workbench.sqlite")
    try:
        with ControlStore(workspace) as control:
            control.create_conversation(
                conversation_id="conv_skills",
                title="Skills",
                goal="Verify frozen Skills",
                status="working",
                metadata={},
                mode="direct",
                primary_agent_id="codex",
            )
            binding = bind_product_skill(
                workspace,
                control,
                "trusted-delivery-test",
                workbench=workbench,
                required=True,
            )
            control.create_agent_session(
                session_id="session_skills",
                conversation_id="conv_skills",
                assignment_id=None,
                current_assignment_id=None,
                agent_id="codex",
                cli_id="codex",
                status="ready",
                worktree=str(workspace),
                transcript_path=str(workspace / ".muxdev" / "skills.ndjson"),
                generation=3,
                control_token_hash=token_hash,
                token_expires_at=(
                    datetime.now(UTC) + timedelta(minutes=10)
                ).isoformat(),
            )
            freeze_bound_skills(
                workspace,
                control,
                conversation_id="conv_skills",
                assignment_id=None,
            )

        original_revision = str(binding["revision"])
        (skill_dir / "SKILL.md").write_text(
            (skill_dir / "SKILL.md")
            .read_text(encoding="utf-8")
            .replace("original frozen instructions", "drifted live instructions"),
            encoding="utf-8",
        )
        drifted = find_catalog_skill(
            build_skill_catalog(workspace),
            "trusted-delivery-test",
        )
        assert drifted["revision"] != original_revision

        loaded = load_product_skill(
            workspace,
            "trusted-delivery-test",
            control_token=raw_token,
        )
        assert "original frozen instructions" in loaded["content"]
        assert "drifted live instructions" not in loaded["content"]
        assert loaded["revision"] == original_revision
        assert loaded["capture_grade"] == "verified"
        with pytest.raises(PermissionError, match="invalid"):
            load_product_skill(
                workspace,
                "trusted-delivery-test",
                control_token="wrong-token",
            )

        with ControlStore(workspace) as control:
            events = control.conversation_events("conv_skills")
            assert events[-1]["type"] == "skill.loaded"
            assert events[-1]["capture_grade"] == "verified"
            context = SimpleNamespace(
                conversation_id="conv_skills",
                skill_bindings=(binding,),
                run_id="run_skill_gate",
                subject_digest="sha256:subject",
            )
            records: list[object] = []
            manifest = _record_skill_evidence(
                SimpleNamespace(store=control),
                context,
                records,
            )
            policy = _extend_skill_policy(
                EvidencePolicy(policy_id="skill-gate", requirements=[]),
                (binding,),
            )
            assert evaluate_gate(
                policy,
                records,
                subject_digest=context.subject_digest,
            ).status == "PASS"
            assert manifest[0].capture_grade == "verified"

            control.update_agent_session("session_skills", generation=4)
            next_records: list[object] = []
            next_manifest = _record_skill_evidence(
                SimpleNamespace(store=control),
                context,
                next_records,
            )
            decision = evaluate_gate(
                policy,
                next_records,
                subject_digest=context.subject_digest,
            )
            assert decision.status == "BLOCKED"
            assert next_manifest[-1].capture_grade == "unavailable"
            snapshot = control.list_skill_snapshots("conv_skills")[0]
            frozen_file = Path(str(snapshot["snapshot_path"])) / "SKILL.md"
            frozen_file.chmod(stat.S_IREAD | stat.S_IWRITE)
            frozen_file.write_text("tampered snapshot", encoding="utf-8")
        with pytest.raises(SkillCatalogError, match="hash mismatch"):
            load_product_skill(
                workspace,
                "trusted-delivery-test",
                control_token=raw_token,
            )
    finally:
        workbench.close()
