"""Built-in capability descriptors."""

from __future__ import annotations

from ..contracts import CapabilityDescriptor


def builtin_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        CapabilityDescriptor("git.diff", owner="core", side_effect="read"),
        CapabilityDescriptor("git.snapshot", owner="core", side_effect="read"),
        CapabilityDescriptor("worktree.prepare", owner="core", side_effect="write", required_approval="write"),
        CapabilityDescriptor("evidence.write_bundle", owner="core", side_effect="write"),
        CapabilityDescriptor("evidence.verify", owner="core", side_effect="read"),
        CapabilityDescriptor("memory.query", owner="core", side_effect="read"),
        CapabilityDescriptor("approval.request", owner="core", side_effect="external", required_approval="approval"),
    )
