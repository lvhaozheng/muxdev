from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from muxdev.api.web import create_app
from muxdev.domain import HarnessPolicySpec, ReviewAssignment, RoutingPolicySpec, RunSpec
from muxdev.models import ApprovalStatus, RunStatus
from muxdev.providers import MockProviderAdapter, ReplayAdapter
from muxdev.services.routing import beta_quality_lower_bound, extract_task_features, replay_route, route_task
from muxdev.services.routing_benchmark import (
    EXPECTED_CLASSES,
    build_benchmark_plan,
    load_registered_routing_suite,
)
from muxdev.services.routing_review import (
    ensure_reviewer_policy,
    incomplete_current_reviews,
    prepare_review_snapshot,
    verify_review_snapshot,
)
from muxdev.runtime import SupervisorRuntime
from muxdev.storage import Blackboard


@pytest.fixture
def workspace() -> Path:
    root = Path(".test_workspaces") / f"v02_routing_{uuid4().hex}"
    root.mkdir(parents=True)
    (root / ".muxdev").mkdir()
    (root / "src").mkdir()
    (root / "src" / "service.py").write_text("def service():\n    return 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    try:
        yield root.resolve()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _create_run(board: Blackboard, workspace: Path, run_id: str, *, provider: str = "auto") -> None:
    board.create_run(
        run_id=run_id,
        task="fix the failing service tests",
        workflow="software-dev",
        provider=provider,
        workspace=workspace,
        worktree=workspace,
    )


def _normal_policy(*, reviewer_policy: str = "disabled") -> tuple[HarnessPolicySpec, RoutingPolicySpec]:
    return (
        HarnessPolicySpec(),
        RoutingPolicySpec(
            mode="auto",
            delivery_mode="simulation",
            fixed_provider="mock",
            reviewer_policy=reviewer_policy,
            max_cost_usd=0.5,
        ),
    )


def _features(
    board: Blackboard,
    workspace: Path,
    run_id: str,
    harness: HarnessPolicySpec,
    routing: RoutingPolicySpec,
):
    features = extract_task_features(
        run_id=run_id,
        task="fix the failing service tests",
        workspace=workspace,
        harness_policy=harness,
        routing_policy=routing,
    )
    return board.record_task_feature_set(features)


def test_task_features_are_bounded_deterministic_and_do_not_store_source_or_paths(workspace: Path) -> None:
    harness, routing = _normal_policy()
    first = extract_task_features(
        run_id="run-features",
        task="fix SECRET_TOKEN=do-not-store",
        workspace=workspace,
        harness_policy=harness,
        routing_policy=routing,
    )
    second = extract_task_features(
        run_id="run-features",
        task="fix SECRET_TOKEN=do-not-store",
        workspace=workspace,
        harness_policy=harness,
        routing_policy=routing,
    )
    payload = first.to_dict()
    assert first.source_hash == second.source_hash
    assert payload["repository_file_count"] == 2
    assert "python" in payload["languages"]
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "SECRET_TOKEN" not in encoded
    assert str(workspace) not in encoded
    assert ".muxdev" not in encoded


def test_quality_lower_bound_rewards_verified_evidence() -> None:
    _alpha, _beta, sparse = beta_quality_lower_bound(0, 0)
    _alpha, _beta, proven = beta_quality_lower_bound(20, 1)
    _alpha, _beta, failing = beta_quality_lower_bound(1, 20)
    assert proven > sparse > failing


def test_route_decision_is_hash_bound_idempotent_and_updates_run_projection(workspace: Path) -> None:
    published: list[dict[str, object]] = []
    with Blackboard(workspace / "run", event_sink=published.append) as board:
        _create_run(board, workspace, "run-route")
        harness, routing = _normal_policy()
        features = _features(board, workspace, "run-route", harness, routing)
        adapters = {"mock": MockProviderAdapter(), "replay": ReplayAdapter([])}
        first = route_task(board, feature_set=features, routing_policy=routing, harness_policy=harness, adapters=adapters)
        second = route_task(board, feature_set=features, routing_policy=routing, harness_policy=harness, adapters=adapters)
        assert first.decision_id == second.decision_id
        assert first.selected_main_provider == "mock"
        assert board.get_run("run-route")["provider"] == "mock"
        assert [event["type"] for event in published].count("route_decision") == 1
        assert replay_route(board, run_id="run-route")["production_evidence"] is False


def test_auto_route_uses_only_complete_mode_matched_outcomes(workspace: Path) -> None:
    with Blackboard(workspace / "run") as board:
        for index in range(10):
            mock_run = f"mock-history-{index}"
            replay_run = f"replay-history-{index}"
            _create_run(board, workspace, mock_run, provider="mock")
            _create_run(board, workspace, replay_run, provider="replay")
            board.record_routing_outcome(
                run_id=mock_run,
                decision_id=None,
                provider="mock",
                task_type="test",
                verified_success=False,
                quality_score=0.2,
                evidence_complete=True,
                source="simulation",
            )
            board.record_routing_outcome(
                run_id=replay_run,
                decision_id=None,
                provider="replay",
                task_type="test",
                verified_success=True,
                quality_score=1.0,
                evidence_complete=True,
                source="simulation",
            )
        _create_run(board, workspace, "incomplete", provider="mock")
        assert board.record_routing_outcome(
            run_id="incomplete",
            decision_id=None,
            provider="mock",
            task_type="test",
            verified_success=True,
            quality_score=1.0,
            evidence_complete=False,
            source="simulation",
        ) is None
        _create_run(board, workspace, "run-learned")
        harness, routing = _normal_policy()
        features = _features(board, workspace, "run-learned", harness, routing)
        decision = route_task(
            board,
            feature_set=features,
            routing_policy=routing,
            harness_policy=harness,
            adapters={"mock": MockProviderAdapter(), "replay": ReplayAdapter([])},
        )
        assert decision.selected_main_provider == "replay"
        assert next(item for item in decision.candidates if item.provider == "replay").sample_count == 10


def test_route_record_rolls_back_without_post_commit_broadcast(workspace: Path) -> None:
    published: list[dict[str, object]] = []
    with Blackboard(workspace / "run", event_sink=published.append) as board:
        _create_run(board, workspace, "run-rollback")
        harness, routing = _normal_policy()
        features = _features(board, workspace, "run-rollback", harness, routing)
        decision = route_task(
            board,
            feature_set=features,
            routing_policy=routing,
            harness_policy=harness,
            adapters={"mock": MockProviderAdapter()},
            idempotency_key="prepare-decision",
        )
        published.clear()
        replacement = replace(
            decision,
            decision_id=f"route_{uuid4().hex}",
            idempotency_key="rolled-back",
            decision_hash="",
        )
        replacement = replacement.create(**{
            key: value
            for key, value in replacement.__dict__.items()
            if key not in {"decision_id", "decision_hash"}
        })
        with pytest.raises(RuntimeError, match="rollback"):
            with board.unit_of_work():
                board.record_route_decision(replacement)
                raise RuntimeError("rollback")
        assert board.get_route_decision("run-rollback", idempotency_key="rolled-back") is None
        assert published == []


def test_concurrent_identical_route_requests_create_one_decision(workspace: Path) -> None:
    database = workspace / "routing.sqlite"
    harness, routing = _normal_policy()
    with Blackboard(workspace / "run", db_path=database) as board:
        _create_run(board, workspace, "run-concurrent")
        features = _features(board, workspace, "run-concurrent", harness, routing)

    def decide(_index: int) -> str:
        with Blackboard(workspace / "run", db_path=database) as board:
            decision = route_task(
                board,
                feature_set=features,
                routing_policy=routing,
                harness_policy=harness,
                adapters={"mock": MockProviderAdapter(), "replay": ReplayAdapter([])},
            )
            return decision.decision_id

    with ThreadPoolExecutor(max_workers=10) as pool:
        decision_ids = list(pool.map(decide, range(20)))
    assert len(set(decision_ids)) == 1
    with Blackboard(workspace / "run", db_path=database) as board:
        assert len(board.list_route_decisions("run-concurrent")) == 1


def test_high_risk_route_assigns_distinct_read_only_reviewer_and_freezes_snapshot(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Blackboard(workspace / "run") as board:
        _create_run(board, workspace, "run-review")
        harness = HarnessPolicySpec.derive(task="security migration", gate="strict", automation={})
        routing = RoutingPolicySpec(
            mode="auto",
            delivery_mode="simulation",
            fixed_provider="mock",
            reviewer_policy="required",
        )
        features = _features(board, workspace, "run-review", harness, routing)
        decision = route_task(
            board,
            feature_set=features,
            routing_policy=routing,
            harness_policy=harness,
            adapters={"mock": MockProviderAdapter(), "replay": ReplayAdapter([])},
        )
        assert decision.selected_main_provider == "mock"
        assert decision.selected_reviewer_provider == "replay"
        assert decision.candidates[0].executable_fingerprint != decision.candidates[1].executable_fingerprint
        preflight = ensure_reviewer_policy(
            board,
            run_id="run-review",
            decision=decision,
            harness_policy=harness,
            routing_policy=routing,
        )
        assert preflight.status == "ready"
        artifact = workspace / "run" / "artifact.txt"
        artifact.write_text("delivery", encoding="utf-8")
        board.add_artifact("run-review", "code", artifact.name, artifact, "delivery")
        monkeypatch.setattr("muxdev.services.routing_review._git_diff", lambda _path: "diff --git a/a b/a\n+safe\n")
        snapshot, path = prepare_review_snapshot(
            board,
            run_dir=workspace / "run",
            run_id="run-review",
            stage_id="review",
            worktree=workspace,
            reviewer_provider="replay",
        )
        assert verify_review_snapshot(path, str(snapshot["snapshot_hash"]))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["patch_hash"] = "sha256:tampered"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert not verify_review_snapshot(path, str(snapshot["snapshot_hash"]))


def test_missing_reviewer_requires_subject_bound_waiver(workspace: Path) -> None:
    with Blackboard(workspace / "run") as board:
        _create_run(board, workspace, "run-waiver")
        harness = HarnessPolicySpec.derive(task="security migration", gate="strict", automation={})
        routing = RoutingPolicySpec(
            mode="auto",
            delivery_mode="simulation",
            fixed_provider="mock",
            reviewer_policy="required",
        )
        features = _features(board, workspace, "run-waiver", harness, routing)
        decision = route_task(
            board,
            feature_set=features,
            routing_policy=routing,
            harness_policy=harness,
            adapters={"mock": MockProviderAdapter()},
        )
        waiting = ensure_reviewer_policy(
            board,
            run_id="run-waiver",
            decision=decision,
            harness_policy=harness,
            routing_policy=routing,
        )
        assert waiting.status == "awaiting_approval"
        board.decide_approval(str(waiting.approval_id), ApprovalStatus.APPROVED)
        ready = ensure_reviewer_policy(
            board,
            run_id="run-waiver",
            decision=decision,
            harness_policy=harness,
            routing_policy=routing,
        )
        assert ready.status == "ready"
        assert ready.assignment and ready.assignment["status"] == "waived"
        changed = replace(decision, decision_hash="sha256:changed")
        changed_waiting = ensure_reviewer_policy(
            board,
            run_id="run-waiver",
            decision=changed,
            harness_policy=harness,
            routing_policy=routing,
        )
        assert changed_waiting.status == "awaiting_approval"
        assert changed_waiting.approval_id != waiting.approval_id


def test_high_risk_cannot_disable_independent_review(workspace: Path) -> None:
    with Blackboard(workspace / "run") as board:
        _create_run(board, workspace, "run-review-required")
        harness = HarnessPolicySpec.derive(task="security migration", gate="strict", automation={})
        routing = RoutingPolicySpec(
            mode="auto",
            delivery_mode="simulation",
            fixed_provider="mock",
            reviewer_policy="disabled",
        )
        features = _features(board, workspace, "run-review-required", harness, routing)
        decision = route_task(
            board,
            feature_set=features,
            routing_policy=routing,
            harness_policy=harness,
            adapters={"mock": MockProviderAdapter()},
        )
        assert decision.selected_reviewer_provider is None
        assert "heterogeneous_reviewer_unavailable" in decision.reason_codes
        waiting = ensure_reviewer_policy(
            board,
            run_id="run-review-required",
            decision=decision,
            harness_policy=harness,
            routing_policy=routing,
        )
        assert waiting.status == "awaiting_approval"


def test_successful_review_retry_supersedes_blocked_attempt_for_completion(workspace: Path) -> None:
    with Blackboard(workspace / "run") as board:
        _create_run(board, workspace, "run-review-retry")
        harness = HarnessPolicySpec.derive(task="security migration", gate="strict", automation={})
        routing = RoutingPolicySpec(
            mode="auto",
            delivery_mode="simulation",
            fixed_provider="mock",
            reviewer_policy="required",
        )
        features = _features(board, workspace, "run-review-retry", harness, routing)
        decision = route_task(
            board,
            feature_set=features,
            routing_policy=routing,
            harness_policy=harness,
            adapters={"mock": MockProviderAdapter(), "replay": ReplayAdapter([])},
        )
        first = ensure_reviewer_policy(
            board,
            run_id="run-review-retry",
            decision=decision,
            harness_policy=harness,
            routing_policy=routing,
        ).assignment
        assert first
        board.update_review_assignment(str(first["review_id"]), status="blocked", verdict="reject")
        board.record_review_assignment(
            ReviewAssignment.create(
                run_id="run-review-retry",
                route_decision_id=decision.decision_id,
                reviewer_provider="replay",
                main_provider="mock",
                status="completed",
                attempt=2,
                verdict="accept",
            )
        )
        assert incomplete_current_reviews(board, "run-review-retry") == []


def test_runspec_v2_imports_conservative_fixed_routing_policy(workspace: Path) -> None:
    spec = RunSpec.from_submit_payload(
        run_id="legacy-v2",
        task="legacy",
        workspace=workspace,
        provider="mock",
        workflow="software-dev",
    )
    payload = spec.to_payload()
    payload["schema_version"] = 2
    payload.pop("routing_policy")
    imported = RunSpec.from_payload(payload)
    assert imported.to_payload()["schema_version"] == 4
    assert imported.routing_policy.mode == "fixed"
    assert imported.routing_policy.fixed_provider == "mock"


def test_registered_benchmark_has_six_classes_and_live_guardrails() -> None:
    suite = load_registered_routing_suite("trusted-routing-v1")
    assert len(suite["cases"]) == 24
    assert EXPECTED_CLASSES == {item["class"] for item in suite["cases"]}
    offline = build_benchmark_plan(suite)
    assert offline["planned_attempts"] == 48
    with pytest.raises(ValueError, match="--yes"):
        build_benchmark_plan(suite, live=True, max_cost_usd=1.0)
    with pytest.raises(ValueError, match="max-cost-usd"):
        build_benchmark_plan(suite, live=True, acknowledged=True)


def test_http_surface_has_read_models_and_no_force_route_mutation() -> None:
    app = create_app(task_manager=object())
    routes = {(route.path, method) for route in app.routes for method in getattr(route, "methods", set())}
    assert ("/api/tasks/{task_id}/route", "GET") in routes
    assert ("/api/tasks/{task_id}/review", "GET") in routes
    assert ("/api/routing/snapshots", "GET") in routes
    assert ("/api/routing/replays", "POST") in routes
    assert ("/api/routing/benchmarks", "POST") in routes
    assert not any("force" in path or "score" in path for path, _method in routes if path.startswith("/api/routing"))


@pytest.mark.integration
def test_high_risk_runtime_completes_only_after_heterogeneous_review(workspace: Path) -> None:
    state_db = workspace / "runtime.sqlite"
    result = SupervisorRuntime(workspace, state_db=state_db, write_dashboards=False).run(
        "security migration with tests",
        provider="mock",
        workflow_name="software-dev",
        gate="strict",
        routing_policy={
            "mode": "auto",
            "delivery_mode": "simulation",
            "fixed_provider": "mock",
            "reviewer_policy": "required",
        },
    )
    assert result.status == RunStatus.COMPLETED
    with Blackboard(result.run_dir, db_path=state_db) as board:
        decision = board.latest_route_decision(result.run_id, kind="main")
        assignments = board.list_review_assignments(result.run_id)
        assert decision and decision["selected_main_provider"] == "mock"
        assert decision["selected_reviewer_provider"] == "replay"
        assert assignments[-1]["status"] == "completed"
        assert assignments[-1]["snapshot_hash"]
