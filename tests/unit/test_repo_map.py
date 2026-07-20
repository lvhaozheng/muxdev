from __future__ import annotations

from pathlib import Path

from muxdev.services.repo_map import build_repo_map


def test_repo_map_is_deterministic_and_symbol_based(workspace: Path) -> None:
    source = workspace / "src" / "payment_router.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class PaymentRouter:\n    def route_payment(self, amount):\n        return amount\n",
        encoding="utf-8",
    )
    first = build_repo_map(workspace, "change payment routing")
    second = build_repo_map(workspace, "change payment routing")
    assert first == second
    assert first.splitlines()[0].startswith("src/payment_router.py")
    assert "class PaymentRouter" in first
    assert "def route_payment" in first
