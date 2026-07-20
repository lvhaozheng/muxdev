from muxdev.services.router import ProviderRouter, beta_lower_bound


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
