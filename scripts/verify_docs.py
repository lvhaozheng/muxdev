from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PLAN = ROOT / "docs" / "v0.2_trusted_agent_harness_plan.md"
BENCHMARK = ROOT / "benchmarks" / "trusted-routing" / "v1" / "manifest.yaml"
PACKAGED_BENCHMARK = ROOT / "src" / "muxdev" / "benchmarks" / "trusted_routing_v1.yaml"


def main() -> int:
    required = [
        ROOT / "LICENSE",
        PLAN,
        ROOT / "docs" / "v0.2_threat_model.md",
        ROOT / "docs" / "adr" / "0001-v0.2-product-contract.md",
        ROOT / "docs" / "adr" / "0002-event-driven-state-and-reliable-sqlite.md",
        ROOT / "docs" / "adr" / "0003-durable-runtime-and-safe-recovery.md",
        ROOT / "docs" / "adr" / "0004-certified-adapters-and-tiered-isolation.md",
        ROOT / "docs" / "adr" / "0005-quality-first-routing-and-heterogeneous-review.md",
        ROOT / "docs" / "adr" / "0006-project-signing-and-offline-trust.md",
        ROOT / "docs" / "adr" / "0007-task-story-and-no-build-dashboard.md",
        ROOT / "docs" / "adr" / "0008-dual-track-benchmark-and-local-rc.md",
        ROOT / "docs" / "v0.2_week2_state_storage.md",
        ROOT / "docs" / "v0.2_week3_durable_runtime.md",
        ROOT / "docs" / "v0.2_week4_certified_harness.md",
        ROOT / "docs" / "v0.2_week5_capability_routing_review.md",
        ROOT / "docs" / "v0.2_week6_signed_delivery_attestation.md",
        ROOT / "docs" / "v0.2_week7_task_first_product.md",
        ROOT / "docs" / "v0.2_week8_benchmark_release.md",
        ROOT / "docs" / "demo" / "v0.2_four_minute_demo.md",
        ROOT / "docs" / "muxattest_format.md",
        ROOT / "docs" / "benchmarks" / "trusted-routing-bench.md",
        ROOT / "CHANGELOG.md",
        ROOT / "SECURITY.md",
    ]
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.is_file()]
    if missing:
        raise SystemExit("missing required v0.2 documents: " + ", ".join(missing))
    if BENCHMARK.read_text(encoding="utf-8").strip() != PACKAGED_BENCHMARK.read_text(encoding="utf-8").strip():
        raise SystemExit("repository and packaged trusted-routing manifests differ")

    readme = README.read_text(encoding="utf-8")
    plan = PLAN.read_text(encoding="utf-8")
    for label in ("当前已实现", "v0.2 目标"):
        if label not in readme or label not in plan:
            raise SystemExit(f"README and plan must both distinguish status label: {label}")

    broken: list[str] = []
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", readme):
        path_text = target.split("#", 1)[0].strip()
        if not path_text or "://" in path_text or path_text.startswith("#"):
            continue
        if not (ROOT / path_text).exists():
            broken.append(target)
    if broken:
        raise SystemExit("broken README links: " + ", ".join(broken))
    print("documentation contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
