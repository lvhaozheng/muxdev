from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.api.web import create_app
from muxdev.cli import app
from muxdev.services.skills import (
    activate_skill,
    bind_skill,
    build_skill_catalog,
    resolve_active_skills,
    scan_skills,
    select_skills,
    set_skill_policy,
    skill_doctor,
    verify_skill_lock,
    write_skill_lock,
)


runner = CliRunner()


def test_catalog_is_metadata_only_and_activation_loads_skill_md() -> None:
    workspace = _workspace_temp("skill-catalog")
    try:
        _write_skill(workspace / ".agents" / "skills" / "secure-review", name="secure-review", description="Review security sensitive code")

        catalog = build_skill_catalog(workspace).to_dict()
        activated = activate_skill(workspace, "secure-review", role="review", provider="mock").to_dict(include_content=True)

        row = catalog["skills"][0]
        assert row["name"] == "secure-review"
        assert "content" not in row
        assert "content" in activated
        assert (workspace / ".muxdev" / "skill-events.jsonl").exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_untrusted_skill_is_visible_but_not_auto_selected() -> None:
    workspace = _workspace_temp("skill-trust")
    try:
        _write_skill(workspace / ".agents" / "skills" / "secure-review", name="secure-review", description="Review security sensitive code")

        selection = select_skills(workspace, task="review security sensitive code", roles=["review"], provider="mock").to_dict()
        set_skill_policy(workspace, "secure-review", trust="project_trusted")
        trusted = select_skills(workspace, task="review security sensitive code", roles=["review"], provider="mock").to_dict()

        assert "secure-review" not in {row["name"] for row in selection["selected"]}
        assert selection["warnings"]
        assert trusted["selected"][0]["name"] == "secure-review"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_invalid_skill_is_excluded_from_catalog_but_visible_to_doctor() -> None:
    workspace = _workspace_temp("skill-invalid")
    try:
        skill_dir = workspace / "skills" / "broken"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: [unterminated\n---\n# Broken\n", encoding="utf-8")

        catalog_rows = scan_skills(workspace)
        doctor = skill_doctor(workspace)

        assert all(row.name != "broken" for row in catalog_rows)
        assert doctor["valid"] is False
        assert "frontmatter invalid" in " ".join(doctor["errors"])
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_role_binding_allows_manual_project_activation() -> None:
    workspace = _workspace_temp("skill-binding")
    try:
        _write_skill(workspace / ".agents" / "skills" / "repo-style", name="repo-style", description="Follow repository style")
        bind_skill(workspace, "code", "repo-style")

        active = resolve_active_skills(workspace, task="implement feature", roles=["code"], provider="mock", include_content=False)

        assert active[0]["name"] == "repo-style"
        assert active[0]["reason"] == "role_binding"
        assert "content" not in active[0]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_builtin_role_skills_are_trusted_and_auto_selected() -> None:
    workspace = _workspace_temp("builtin-role-skills")
    try:
        skills = {skill.name: skill for skill in scan_skills(workspace)}
        active = resolve_active_skills(workspace, task="implement feature", roles=["code"], provider="mock")

        assert skills["default-code"].trust == "builtin_trusted"
        assert "code" in skills["default-code"].roles
        assert "default-code" in {row["name"] for row in active}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_skill_lock_v2_detects_script_drift() -> None:
    workspace = _workspace_temp("skill-lock")
    try:
        skill_dir = workspace / "skills" / "reviewer"
        _write_skill(skill_dir, name="reviewer", description="Review code")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "check.py").write_text("print('one')\n", encoding="utf-8")

        locked = write_skill_lock(workspace, promote_memory=False)
        before = verify_skill_lock(workspace)
        (skill_dir / "scripts" / "check.py").write_text("print('two')\n", encoding="utf-8")
        after = verify_skill_lock(workspace)

        assert locked["skills"][0]["hashes"]["tree"].startswith("sha256:")
        assert before["valid"] is True
        assert after["valid"] is False
        assert any("scripts" in error or "tree" in error for error in after["errors"])
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_skills_api_exposes_catalog_and_lock(monkeypatch) -> None:
    workspace = _workspace_temp("skill-api")
    try:
        monkeypatch.setenv("MUXDEV_HOME", str(workspace / "home"))
        _write_skill(workspace / "skills" / "docs-update", name="docs-update", description="Update documentation")
        write_skill_lock(workspace, promote_memory=False)
        client = TestClient(create_app())

        catalog = client.get("/api/skills/catalog", params={"workspace": str(workspace)}).json()
        lock = client.get("/api/skills/lock", params={"workspace": str(workspace)}).json()
        skills_page = client.get("/skills", params={"workspace": str(workspace)})
        scorecards = client.get("/api/skills/scorecards", params={"workspace": str(workspace)}).json()

        assert catalog["skills"][0]["name"] == "docs-update"
        assert "content" not in catalog["skills"][0]
        assert lock["valid"] is True
        assert skills_page.status_code == 200
        assert "docs-update" in skills_page.text
        assert scorecards[0]["skill"] == "docs-update"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_skill_cli_catalog_explain_and_verify() -> None:
    workspace = _workspace_temp("skill-cli")
    try:
        _write_skill(workspace / "skills" / "docs-update", name="docs-update", description="Update documentation")
        with _chdir(workspace):
            trusted = runner.invoke(app, ["skill", "trust", "docs-update", "project_trusted", "--json"])
            locked = runner.invoke(app, ["skill", "lock", "--no-memory", "--json"])
            catalog = runner.invoke(app, ["skill", "catalog", "--role", "docs", "--json"])
            explained = runner.invoke(app, ["skill", "explain", "--task", "update documentation", "--role", "docs", "--json"])
            verified = runner.invoke(app, ["skill", "verify", "--lock", "--json"])

        assert trusted.exit_code == 0
        assert locked.exit_code == 0
        assert catalog.exit_code == 0
        assert explained.exit_code == 0
        assert verified.exit_code == 0
        assert json_loads(catalog.stdout)["skills"][0]["name"] == "docs-update"
        assert json_loads(explained.stdout)["selected"][0]["name"] == "docs-update"
        assert json_loads(verified.stdout)["valid"] is True
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _write_skill(path: Path, *, name: str, description: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nkeywords: [security, review, docs]\nmetadata:\n  compatible_roles: [review, code, docs]\n---\n# {name}\n",
        encoding="utf-8",
    )


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def json_loads(text: str):
    import json

    return json.loads(text)


@contextmanager
def _chdir(path: Path) -> Iterator[None]:
    cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(cwd)
