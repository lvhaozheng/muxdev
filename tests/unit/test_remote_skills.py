from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muxdev.api import create_app
from muxdev.services.skills.catalog import audit_skill_source
from muxdev.services.skills import remote_catalog
from muxdev.services.skills.remote_catalog import (
    RemoteSkillError,
    download_remote_skill,
    parse_github_skill_url,
    search_remote_skills,
)


COMMIT = "a" * 40


class FakeGitHubClient:
    def __init__(
        self,
        *,
        tree: list[dict[str, object]],
        files: dict[str, bytes],
    ) -> None:
        self._tree = tree
        self._files = files
        self.resolve_calls: list[tuple[str, str, str]] = []

    def resolve_commit(self, owner: str, repo: str, ref: str) -> str:
        self.resolve_calls.append((owner, repo, ref))
        return COMMIT

    def tree(self, owner: str, repo: str, commit: str) -> list[dict[str, object]]:
        assert commit == COMMIT
        return self._tree

    def read_file(
        self,
        owner: str,
        repo: str,
        commit: str,
        path: str,
        *,
        limit: int = 256 * 1024,
    ) -> bytes:
        assert commit == COMMIT
        payload = self._files[path]
        assert len(payload) <= limit
        return payload


def _blob(path: str, size: int, *, mode: str = "100644") -> dict[str, object]:
    return {
        "path": path,
        "type": "blob",
        "mode": mode,
        "size": size,
        "sha": "b" * 40,
    }


def test_remote_catalog_searches_official_provider_and_returns_pinned_source() -> None:
    skill = (
        b"---\nname: review-helper\n"
        b"description: Review changes with an evidence checklist.\n---\n# Review\n"
    )
    license_text = b"Apache License\nVersion 2.0, January 2004\n"
    tree = [
        _blob("skills/.curated/review-helper/SKILL.md", len(skill)),
        _blob("skills/.curated/review-helper/LICENSE.txt", len(license_text)),
        _blob("skills/.curated/review-helper/scripts/check.py", 10),
    ]
    client = FakeGitHubClient(
        tree=tree,
        files={
            "skills/.curated/review-helper/SKILL.md": skill,
            "skills/.curated/review-helper/LICENSE.txt": license_text,
        },
    )
    remote_catalog._TREE_CACHE.clear()

    result = search_remote_skills(
        "review helper",
        provider="openai",
        client=client,  # type: ignore[arg-type]
    )

    assert result["total"] == 1
    item = result["items"][0]
    assert item["name"] == "review-helper"
    assert item["commit_sha"] == COMMIT
    assert f"/tree/{COMMIT}/" in item["source_url"]
    assert item["license"] == "Apache-2.0"
    assert item["script_count"] == 1


def test_remote_download_is_bounded_and_content_audited(workspace: Path) -> None:
    skill = b"---\nname: demo\ndescription: Demo Skill.\n---\n# Demo\n"
    script = b"print('ok')\n"
    tree = [
        _blob("demo/SKILL.md", len(skill)),
        _blob("demo/scripts/check.py", len(script)),
    ]
    client = FakeGitHubClient(
        tree=tree,
        files={
            "demo/SKILL.md": skill,
            "demo/scripts/check.py": script,
        },
    )

    result = download_remote_skill(
        "https://github.com/example/skills/tree/main/demo",
        workspace / "remote-download",
        client=client,  # type: ignore[arg-type]
    )

    assert result["location"]["commit_sha"] == COMMIT
    assert result["audit"]["skill_count"] == 1
    assert (workspace / "remote-download" / "SKILL.md").read_bytes() == skill
    assert result["audit"]["revision"].startswith("sha256:")


def test_remote_download_rejects_symlinks_and_oversized_files(workspace: Path) -> None:
    skill = b"---\nname: demo\ndescription: Demo Skill.\n---\n# Demo\n"
    symlink_client = FakeGitHubClient(
        tree=[
            _blob("demo/SKILL.md", len(skill)),
            _blob("demo/outside", 8, mode="120000"),
        ],
        files={"demo/SKILL.md": skill, "demo/outside": b"../secret"},
    )
    with pytest.raises(RemoteSkillError, match="symbolic"):
        download_remote_skill(
            "https://github.com/example/skills/tree/main/demo",
            workspace / "remote-symlink",
            client=symlink_client,  # type: ignore[arg-type]
        )

    large_client = FakeGitHubClient(
        tree=[
            _blob("demo/SKILL.md", len(skill)),
            _blob("demo/large.bin", 256 * 1024 + 1),
        ],
        files={"demo/SKILL.md": skill},
    )
    with pytest.raises(RemoteSkillError, match="256 KiB"):
        download_remote_skill(
            "https://github.com/example/skills/tree/main/demo",
            workspace / "remote-large",
            client=large_client,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example/skills/tree/main/demo",
        "https://user:token@github.com/example/skills/tree/main/demo",
        "https://gitlab.com/example/skills/tree/main/demo",
        "https://github.com/example/skills/tree/main/../demo",
        "https://github.com/example/skills/blob/main/demo/README.md",
    ],
)
def test_remote_url_rejects_unsafe_or_unsupported_locations(url: str) -> None:
    with pytest.raises(RemoteSkillError):
        parse_github_skill_url(url)


def test_remote_import_api_is_idempotent_and_never_auto_trusts(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_download(
        url: str,
        destination: Path,
        *,
        ref: str | None = None,
    ) -> dict[str, object]:
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text(
            "---\nname: downloaded-demo\ndescription: Downloaded test Skill.\n---\n# Demo\n",
            encoding="utf-8",
        )
        return {
            "location": {
                "owner": "example",
                "repo": "skills",
                "repository": "example/skills",
                "path": "demo",
                "requested_ref": ref or "main",
                "commit_sha": COMMIT,
                "source_url": (
                    f"https://github.com/example/skills/tree/{COMMIT}/demo"
                ),
            },
            "license": "Apache-2.0",
            "audit": audit_skill_source(destination),
        }

    monkeypatch.setattr(
        "muxdev.api.skills.download_remote_skill",
        fake_download,
    )
    with TestClient(create_app(workspace)) as client:
        body = {
            "url": "https://github.com/example/skills/tree/main/demo",
            "display_name": "Downloaded Demo",
        }
        first = client.post("/api/v2/skill-sources/import-remote", json=body)
        second = client.post("/api/v2/skill-sources/import-remote", json=body)

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        source = first.json()
        assert second.json()["source_id"] == source["source_id"]
        assert source["trust_state"] == "needs_review"
        assert source["enabled"] is False
        assert source["metadata"]["commit_sha"] == COMMIT
        assert source["metadata"]["license"] == "Apache-2.0"

        project = client.get("/api/v2/projects").json()[0]
        catalog = client.get(
            f"/api/v2/projects/{project['project_id']}/skills"
        ).json()["catalog"]
        downloaded = next(
            item for item in catalog if item["name"] == "downloaded-demo"
        )
        assert downloaded["enabled"] is False
        assert downloaded["trust"] == "needs_review"
