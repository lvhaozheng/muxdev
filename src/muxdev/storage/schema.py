"""Versioned Blackboard schema registry.

The migration callbacks remain on ``Blackboard`` so v7 SQL stays byte-for-byte
compatible; this module owns their ordering and immutable checksums.
"""

from __future__ import annotations

from typing import Any

from .sqlite import Migration


def blackboard_migrations(blackboard: Any) -> tuple[Migration, ...]:
    """Return the complete, append-only migration chain for a Blackboard."""
    return (
        Migration(1, "blackboard_baseline", "blackboard-v1-20260718", blackboard._create_v1_schema),
        Migration(2, "operational_state_events", "blackboard-v2-state-events-attempt-history", blackboard._create_v2_schema),
        Migration(3, "durable_execution_queue", "blackboard-v3-durable-runtime-20260719", blackboard._create_v3_schema),
        Migration(4, "certified_agent_harness", "blackboard-v4-certified-harness-20260719-r2", blackboard._create_v4_schema),
        Migration(5, "quality_routing_and_review", "blackboard-v5-routing-review-20260719-r1", blackboard._create_v5_schema),
        Migration(6, "signed_delivery_attestation", "blackboard-v6-signed-attestation-20260719-r1", blackboard._create_v6_schema),
        Migration(7, "trusted_routing_benchmark", "blackboard-v7-trusted-benchmark-20260719-r1", blackboard._create_v7_schema),
    )
