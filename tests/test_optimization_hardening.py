from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from muxdev.context import apply_context_budget
from muxdev.models import RunStatus
from muxdev.domain import StageExecutionInput, StageExecutionResult
from muxdev.providers.adapters import HeadlessCliProviderAdapter
from muxdev.providers.event_parsers import parser_for
from muxdev.runtime.parallel_merge import (
    ParallelMergeError,
    capture_worker_patch,
    merge_worker_patches,
    prepare_worker_workspace,
    workspace_content_hash,
)
from muxdev.runtime.result_validation import validate_review_result, validate_test_result
from muxdev.runtime.stage_executor import StageExecutor
from muxdev.runtime.supervisor import SupervisorRuntime
from muxdev.storage import Blackboard


@pytest.fixture
def local_tmp() -> Path:
    path = Path(".test_workspaces") / f"hardening_{uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_missing_structured_results_fail_closed() -> None:
    test_result, test_validation = validate_test_result(None, fallback_summary="provider exited zero")
    review_result, review_validation = validate_review_result(None)

    assert test_result.passed is False
    assert test_result.command == "unreported"
    assert test_validation.valid is False
    assert review_result.has_blockers is True
    assert review_result.blockers[0].severity == "high"
    assert review_validation.valid is False
    _, missing_exit = validate_test_result(
        {"passed": True, "command": "pytest -q", "summary": "passed"},
        fallback_summary="provider exited zero",
    )
    assert missing_exit.valid is False
    assert any("exit_code" in error for error in missing_exit.errors)


def test_stage_executor_passes_one_typed_contract(local_tmp: Path) -> None:
    captured: list[StageExecutionInput] = []

    class TypedProvider:
        def execute(self, input: StageExecutionInput) -> StageExecutionResult:
            captured.append(input)
            return StageExecutionResult("session/code.log", "ok", "typed")

    result = StageExecutor(TypedProvider()).execute(
        run_id="run_typed",
        stage_id="code",
        role="code",
        provider="mock",
        task="implement",
        worktree=local_tmp,
        skills=[{"name": "default-code"}],
        session_dir=local_tmp / "sessions",
        attempt=2,
    )

    assert result.summary == "typed"
    assert captured[0].run_id == "run_typed"
    assert captured[0].attempt == 2
    assert captured[0].skills == ({"name": "default-code"},)


def test_blackboard_exposes_narrow_repository_views(local_tmp: Path) -> None:
    with Blackboard(local_tmp) as board:
        board.repositories.lifecycle.create_run(
            run_id="run_repo",
            task="repository boundary",
            workflow="software-dev",
            provider="mock",
            workspace=local_tmp,
            worktree=local_tmp,
        )
        run = board.repositories.lifecycle.get_run("run_repo")
        assert run["task"] == "repository boundary"
        assert board.repositories.transactions.storage_health()["status"] == "healthy"
        with pytest.raises(AttributeError, match="does not expose"):
            board.repositories.evidence.get_run("run_repo")


def test_runtime_blocks_zero_exit_without_test_result(monkeypatch: pytest.MonkeyPatch, local_tmp: Path) -> None:
    workflow = local_tmp / "strict-test.yaml"
    workflow.write_text(
        """name: strict-test
max_parallel: 1
stages:
  - id: test
    role: test
    type: agent
    output_schema: TestResult
""",
        encoding="utf-8",
    )

    class InvalidTestProvider:
        def execute(self, input: StageExecutionInput) -> StageExecutionResult:
            _stage_id, _task, _worktree = input.stage_id, input.task, input.worktree
            return StageExecutionResult("session/test.log", "all tests look good", "provider exited zero", returncode=0)

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda _: InvalidTestProvider())
    result = SupervisorRuntime(local_tmp).run("verify a harmless change", provider="mock", workflow_name=str(workflow))

    assert result.status == RunStatus.BLOCKED
    with Blackboard(result.run_dir) as board:
        rows = board.table_rows("test_results", run_id=result.run_id)
        validations = [row for row in board.table_rows("artifacts", run_id=result.run_id) if row["kind"] == "result_validation"]
    assert rows and rows[-1]["passed"] == 0
    assert validations
    payload = json.loads(Path(validations[-1]["path"]).read_text(encoding="utf-8"))
    assert payload["valid"] is False
    assert payload["provider_returncode"] == 0


def test_context_budget_is_deterministic_and_never_drops_p0_blockers() -> None:
    packet: dict[str, object] = {
        "task": {"task": "login", "provider_action_responses": [], "previous_attempts": []},
        "run": {
            "review_blockers": [{"type": "auth_bypass", "severity": "high", "suggestion": "must fix"}],
            "feedback_events": [],
            "task_memory": [],
            "artifacts": [{"path": f"artifact-{index}", "body": "x" * 1000} for index in range(20)],
            "upstream_artifacts": [],
        },
        "session": {"temporary_context": []},
        "branch": {"feature_memory": []},
        "project": {"long_term_memory": [], "workspace_memory": [], "review_required": []},
        "user_preferences": {"memory": []},
        "rag_context": [],
    }
    config = {"context_budget": {"max_input_tokens": 600, "reserved_output_tokens": 100}}

    first = apply_context_budget(packet, provider="qwen", automation=config)
    second = apply_context_budget(packet, provider="qwen", automation=config)

    assert first == second
    assert first["run"]["review_blockers"]  # type: ignore[index]
    assert first["context_budget"]["omitted"]  # type: ignore[index]
    assert first["context_budget"]["original_packet_hash"].startswith("sha256:")  # type: ignore[index]


def test_parallel_workers_merge_disjoint_content_addressed_patches(local_tmp: Path) -> None:
    base = local_tmp / "base"
    base.mkdir()
    (base / "a.txt").write_text("a0\n", encoding="utf-8")
    (base / "b.txt").write_text("b0\n", encoding="utf-8")
    _init_git(base)
    expected = workspace_content_hash(base)
    workers = local_tmp / "parallel_worktrees"
    left = prepare_worker_workspace(base, workers_root=workers, stage_id="left")
    right = prepare_worker_workspace(base, workers_root=workers, stage_id="right")
    (left.path / "a.txt").write_text("a1\n", encoding="utf-8")
    (right.path / "b.txt").write_text("b1\n", encoding="utf-8")
    patches = [
        capture_worker_patch(left, patches_root=local_tmp / "patches"),
        capture_worker_patch(right, patches_root=local_tmp / "patches"),
    ]

    merged = merge_worker_patches(base, patches, expected_base_hash=expected, fencing_check=lambda: None)

    assert [row["stage_id"] for row in merged] == ["left", "right"]
    assert (base / "a.txt").read_text(encoding="utf-8") == "a1\n"
    assert (base / "b.txt").read_text(encoding="utf-8") == "b1\n"
    assert all(str(row["patch_hash"]).startswith("sha256:") for row in merged)


def test_parallel_merge_rejects_actual_overlapping_writes(local_tmp: Path) -> None:
    base = local_tmp / "base"
    base.mkdir()
    (base / "shared.txt").write_text("base\n", encoding="utf-8")
    _init_git(base)
    expected = workspace_content_hash(base)
    workers = local_tmp / "parallel_worktrees"
    first = prepare_worker_workspace(base, workers_root=workers, stage_id="first")
    second = prepare_worker_workspace(base, workers_root=workers, stage_id="second")
    (first.path / "shared.txt").write_text("first\n", encoding="utf-8")
    (second.path / "shared.txt").write_text("second\n", encoding="utf-8")
    patches = [
        capture_worker_patch(first, patches_root=local_tmp / "patches"),
        capture_worker_patch(second, patches_root=local_tmp / "patches"),
    ]

    with pytest.raises(ParallelMergeError, match="parallel write conflict"):
        merge_worker_patches(base, patches, expected_base_hash=expected, fencing_check=lambda: None)


def test_supervisor_parallel_write_workers_are_isolated_and_merged(
    monkeypatch: pytest.MonkeyPatch,
    local_tmp: Path,
) -> None:
    workflow = local_tmp / "parallel-write.yaml"
    workflow.write_text(
        """name: parallel-write
max_parallel: 2
stages:
  - id: alpha
    role: code
    allow_write: true
  - id: beta
    role: code
    allow_write: true
""",
        encoding="utf-8",
    )

    class WritingProvider:
        def execute(self, input: StageExecutionInput) -> StageExecutionResult:
            stage_id, _task, worktree = input.stage_id, input.task, input.worktree
            (worktree / f"{stage_id}.txt").write_text(f"written by {stage_id}\n", encoding="utf-8")
            return StageExecutionResult(f"session/{stage_id}.log", stage_id, f"wrote {stage_id}")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda _: WritingProvider())
    result = SupervisorRuntime(local_tmp).run("create two independent text files", provider="mock", workflow_name=str(workflow))

    assert result.status == RunStatus.COMPLETED
    assert (local_tmp / "alpha.txt").read_text(encoding="utf-8") == "written by alpha\n"
    assert (local_tmp / "beta.txt").read_text(encoding="utf-8") == "written by beta\n"
    report = json.loads((result.run_dir / "parallel_patches" / "merge_report.json").read_text(encoding="utf-8"))
    assert report["merge_order"] == ["alpha", "beta"]


def test_provider_specific_parser_and_native_resume_template(local_tmp: Path) -> None:
    parser = parser_for("codex")
    events = parser.parse('{"type":"thread.started","thread_id":"thread-1"}\n', returncode=0)
    adapter = HeadlessCliProviderAdapter(
        "codex",
        ["codex", "exec"],
        prompt_transport="stdin",
        resume_command=["codex", "exec", "resume", "{session_id}", "-"],
    )
    handle = adapter.start(stage_id="code", task="continue", worktree=local_tmp, session_id="thread-1")
    resumed = adapter.resume(handle)

    assert events[0].type == "provider.session_started"
    assert parser.session_id('{"type":"thread.started","thread_id":"thread-1"}') == "thread-1"
    assert not hasattr(resumed, "reason")
    assert resumed.metadata["command_override"][-2:] == ["thread-1", "-"]  # type: ignore[union-attr]


def _init_git(path: Path) -> None:
    for args in (
        ("init", "--quiet"),
        ("config", "user.email", "tests@local.invalid"),
        ("config", "user.name", "muxdev tests"),
        ("add", "-A"),
        ("commit", "--quiet", "-m", "baseline"),
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
