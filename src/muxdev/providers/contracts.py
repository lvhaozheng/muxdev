"""Provider runtime contracts.

Adapters can continue to expose the legacy ``run_stage`` shape while richer
providers opt into descriptors, sessions, streaming events, and handoff-aware
resume semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from ..domain.stage import StageExecutionInput, StageExecutionResult
from .registry import CapabilityState, ProviderProbe


class ProviderRuntimeKind(StrEnum):
    MOCK = "mock"
    HEADLESS_CLI = "headless_cli"
    PYTHON = "python"


@dataclass(frozen=True)
class ProviderCapabilities:
    headless: CapabilityState = CapabilityState.UNKNOWN
    pty: CapabilityState = CapabilityState.UNKNOWN
    json: CapabilityState = CapabilityState.UNKNOWN
    approval: CapabilityState = CapabilityState.UNKNOWN
    skill: CapabilityState = CapabilityState.UNKNOWN
    attach: CapabilityState = CapabilityState.UNKNOWN
    patch_output: CapabilityState = CapabilityState.UNKNOWN
    test_execution: CapabilityState = CapabilityState.UNKNOWN
    read_only_mode: CapabilityState = CapabilityState.UNKNOWN

    @classmethod
    def from_probe(cls, probe: ProviderProbe) -> "ProviderCapabilities":
        return cls(
            headless=probe.headless,
            pty=probe.pty,
            json=probe.json,
            approval=probe.approval,
            skill=probe.skill,
            attach=probe.attach,
        )


@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    commands: tuple[str, ...] = ()
    runtime_kind: ProviderRuntimeKind = ProviderRuntimeKind.HEADLESS_CLI
    roles: frozenset[str] = frozenset()
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSession:
    provider: str
    stage_id: str
    worktree: Path
    transcript_path: Path | None = None
    chunks_path: Path | None = None
    attach_command: Sequence[str] | str | None = None


@dataclass(frozen=True)
class ProviderStreamEvent:
    type: str
    text: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderActionDecision:
    action_id: str
    status: str
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderResumeResult:
    status: str
    summary: str = ""
    events: tuple[ProviderStreamEvent, ...] = ()


class ProviderRuntime(Protocol):
    descriptor: ProviderDescriptor

    def probe(self) -> ProviderProbe:
        ...

    def prepare_session(self, ctx: StageExecutionInput) -> ProviderSession:
        ...

    def run_stage(self, ctx: StageExecutionInput) -> StageExecutionResult:
        ...

    def resume_action(self, action: ProviderActionDecision) -> ProviderResumeResult:
        ...
