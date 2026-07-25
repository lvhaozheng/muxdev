from pathlib import Path

from scripts import build_release_candidate


def test_release_build_removes_only_its_generated_build_cache(
    workspace: Path,
    monkeypatch,
) -> None:
    generated = workspace / "build"
    generated.mkdir()
    (generated / "deleted-module.py").write_text("stale", encoding="utf-8")
    adjacent = workspace / "build-not-generated"
    adjacent.mkdir()

    monkeypatch.setattr(build_release_candidate, "ROOT", workspace)
    build_release_candidate._clean_generated_build_tree()

    assert not generated.exists()
    assert adjacent.is_dir()
