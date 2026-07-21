from muxdev.services.router import ProviderRouter, RoutedProvider, beta_lower_bound
from muxdev.storage import ControlStore


def test_beta_lower_bound_rewards_verified_history_conservatively() -> None:
    assert beta_lower_bound(9, 1) > beta_lower_bound(2, 0)
    assert beta_lower_bound(0, 0) < 0.5


def test_history_ignores_untrusted_provider_claims() -> None:
    rows = [
        {"payload": {"success": True, "gate_status": "PASS", "integrity_valid": True, "independent_valid": True}},
        {"payload": {"success": True, "gate_status": "BLOCKED", "integrity_valid": True, "independent_valid": True}},
        {"payload": {"success": True, "gate_status": "PASS", "integrity_valid": False, "independent_valid": True}},
        {"payload": {"success": True, "gate_status": "PASS", "integrity_valid": True, "independent_valid": False}},
        {"payload": {"success": True, "confidence": 1.0, "evidence_score": 100}},
    ]
    history = ProviderRouter._history("provider", rows)
    assert history["successes"] == 1
    assert history["failures"] == 0


def test_role_routing_uses_independent_explicit_reviewer(monkeypatch, workspace) -> None:
    candidates = [
        RoutedProvider(name, True, (), 0, 0, 0.1, 0, 0, score)
        for name, score in (("builder", 0.8), ("reviewer", 0.7))
    ]
    engine_store = ControlStore(workspace)
    router = ProviderRouter(engine_store)
    monkeypatch.setattr(router, "_candidates", lambda **_kwargs: candidates)

    route = router.route(
        "run_roles",
        preferred="builder",
        profile="standard",
        max_cost_usd=1,
        roles=("plan", "code", "review"),
        role_providers={"review": "reviewer"},
    )

    assert route["role_assignments"] == {
        "plan": "builder", "code": "builder", "review": "reviewer"
    }
    assert route["reviewer_provider"] == "reviewer"
    assert route["role_errors"] == []
    engine_store.close()


def test_role_routing_rejects_reviewer_equal_to_implementer(monkeypatch, workspace) -> None:
    candidate = RoutedProvider("builder", True, (), 0, 0, 0.1, 0, 0, 0.8)
    engine_store = ControlStore(workspace)
    router = ProviderRouter(engine_store)
    monkeypatch.setattr(router, "_candidates", lambda **_kwargs: [candidate])

    route = router.route(
        "run_same_reviewer",
        preferred="builder",
        profile="strict",
        max_cost_usd=1,
        roles=("code", "review", "secure"),
        role_providers={"review": "builder", "secure": "builder"},
    )

    assert len(route["role_errors"]) == 2
    assert all("independent" in error for error in route["role_errors"])
    engine_store.close()
