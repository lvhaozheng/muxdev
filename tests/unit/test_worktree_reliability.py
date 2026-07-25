from __future__ import annotations

import sys

from muxdev.runtime.terminal import terminal_capabilities
from muxdev.runtime.worktree import WorktreeManager


def test_fallback_copy_skips_gitignored_and_known_generated_directories(workspace) -> None:
    (workspace / "README.md").write_text("source\n", encoding="utf-8")
    (workspace / ".gitignore").write_text("ignored-output/\n", encoding="utf-8")
    (workspace / "ignored-output").mkdir()
    (workspace / "ignored-output" / "locked.bin").write_bytes(b"ignored")
    (workspace / "release-artifacts").mkdir()
    (workspace / "release-artifacts" / "build.bin").write_bytes(b"generated")
    run_dir = workspace / "run-home"
    run_dir.mkdir()

    result = WorktreeManager(workspace).prepare("copy-test", run_dir)

    assert result.strategy == "workspace_copy"
    assert (result.path / "README.md").is_file()
    assert not (result.path / "ignored-output").exists()
    assert not (result.path / "release-artifacts").exists()


def test_fallback_copy_includes_only_explicit_ignored_files(workspace) -> None:
    (workspace / ".gitignore").write_text(".env*\n", encoding="utf-8")
    (workspace / ".worktreeinclude").write_text(".env.local\n", encoding="utf-8")
    (workspace / ".env.local").write_text("SAFE_FIXTURE=1\n", encoding="utf-8")
    (workspace / ".env.secret").write_text("DO_NOT_COPY=1\n", encoding="utf-8")
    run_dir = workspace / "run-home"
    run_dir.mkdir()

    result = WorktreeManager(workspace).prepare("include-test", run_dir)

    assert (result.path / ".env.local").read_text(encoding="utf-8") == "SAFE_FIXTURE=1\n"
    assert not (result.path / ".env.secret").exists()


def test_worktree_setup_completes_before_prepare_returns(workspace) -> None:
    (workspace / "README.md").write_text("source\n", encoding="utf-8")
    run_dir = workspace / "run-home"
    run_dir.mkdir()

    result = WorktreeManager(workspace).prepare(
        "setup-test",
        run_dir,
        setup_argv=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('setup.ready').write_text('ready')",
        ],
    )

    assert result.message.endswith("worktree setup completed")
    assert (result.path / "setup.ready").read_text(encoding="utf-8") == "ready"


def test_terminal_capabilities_report_pipe_without_claiming_pty() -> None:
    capabilities = terminal_capabilities(supports_pty=False)

    assert capabilities["backend"] == "pipe"
    assert capabilities["pty"] is False
    assert capabilities["resize"] is False
    assert capabilities["process_resume"] is False
