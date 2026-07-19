"""Certified Codex, Qwen, and Replay Agent Harness adapters."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from ..core.platforms import hidden_subprocess_kwargs, script_invocation
from .adapters import HeadlessCliProviderAdapter, ProviderAdapter, ProviderStageOutput, _build_harness_events, _static_probe_env
from .certification import make_certification_report, require_live_acknowledgement, sha256_text
from .harness import (
    ADAPTER_CONTRACT_VERSION,
    AdapterProbe,
    AttemptHandle,
    CancelResult,
    CapabilityVerificationState,
    CertificationReport,
    HarnessEvent,
    HarnessEventSource,
    TrustTier,
    Unsupported,
    capability_map,
)

CODEX_OFFLINE_FIXTURE = (
    '{"type":"thread.started","thread_id":"fixture-thread"}',
    '{"type":"item.completed","item":{"type":"command_execution","command":"echo ok","aggregated_output":"ok","exit_code":0}}',
    '{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":2}}',
)
QWEN_OFFLINE_FIXTURE = (
    '{"type":"system","subtype":"init","session_id":"fixture-session"}',
    '{"type":"tool_use","tool_name":"read_file","tool_id":"tool-1"}',
    '{"type":"tool_result","tool_id":"tool-1","status":"success"}',
    '{"type":"result","subtype":"success","usage":{"input_tokens":3,"output_tokens":2}}',
)


class CodexHarnessAdapter(HeadlessCliProviderAdapter):
    adapter_version = f"codex/{ADAPTER_CONTRACT_VERSION}"
    trust_tier = TrustTier.OPAQUE

    def __init__(self, command: list[str], **kwargs: object) -> None:
        super().__init__("codex", command, **kwargs)
        self.adapter_version = type(self).adapter_version
        self.trust_tier = type(self).trust_tier

    def _decode_provider_events(self, output: str, *, returncode: int) -> list[tuple[str, HarnessEventSource, dict[str, object]]]:
        return decode_codex_jsonl(output, returncode=returncode)

    def _certification_checks(
        self,
        probe: AdapterProbe,
        *,
        live: bool,
        max_cost_usd: float | None,
    ) -> tuple[dict[str, CapabilityVerificationState], dict[str, object], str | None]:
        sandbox_verified = _codex_sandbox_sentinel(probe)
        states = capability_map(
            structured_events="verified",
            tool_events="verified",
            usage="verified",
            session_resume="advertised" if "session_resume" in probe.advertised_capabilities else "unknown",
            cooperative_cancel="verified",
            approval_bridge="unsupported",
            read_only="advertised" if "read_only" in probe.advertised_capabilities else "unknown",
            patch_output="unknown",
            provider_sandbox="verified" if sandbox_verified else (
                "advertised" if _command_has(self.command, "--sandbox", "workspace-write") else "unknown"
            ),
        )
        decoded = decode_codex_jsonl("\n".join(CODEX_OFFLINE_FIXTURE), returncode=0)
        failure = None if any(event[0] == "provider.usage" for event in decoded) else "Codex JSONL fixture did not produce usage"
        checks: list[object] = ["version/help probe", "JSONL fixture", "unknown-event preservation", "process cancellation"]
        if live and failure is None:
            live_ok, live_summary = self._bounded_live_smoke()
            checks.append(live_summary)
            if not live_ok:
                failure = "Codex live smoke test failed"
        return states, {
            "checks": checks,
            "fixture_hashes": [sha256_text(CODEX_OFFLINE_FIXTURE)],
            "sandbox_sentinel": sandbox_verified,
        }, failure

    def _bounded_live_smoke(self) -> tuple[bool, str]:
        original_timeout = self.timeout
        self.timeout = min(self.timeout, 60)
        try:
            with tempfile.TemporaryDirectory(prefix="muxdev-codex-cert-") as temp:
                output = self.run_stage(stage_id="certify", task="Reply exactly MUXDEV_CERT_OK without tools.", worktree=Path(temp))
                return output.returncode == 0, f"live smoke exit={output.returncode}"
        finally:
            self.timeout = original_timeout


class QwenHarnessAdapter(HeadlessCliProviderAdapter):
    adapter_version = f"qwen/{ADAPTER_CONTRACT_VERSION}"
    trust_tier = TrustTier.OPAQUE

    def __init__(self, command: list[str], **kwargs: object) -> None:
        super().__init__("qwen", command, **kwargs)
        self.adapter_version = type(self).adapter_version
        self.trust_tier = type(self).trust_tier

    def _decode_provider_events(self, output: str, *, returncode: int) -> list[tuple[str, HarnessEventSource, dict[str, object]]]:
        return decode_qwen_stream_json(output, returncode=returncode)

    def _certification_checks(
        self,
        probe: AdapterProbe,
        *,
        live: bool,
        max_cost_usd: float | None,
    ) -> tuple[dict[str, CapabilityVerificationState], dict[str, object], str | None]:
        states = capability_map(
            structured_events="verified",
            tool_events="verified",
            usage="verified",
            session_resume="advertised" if "session_resume" in probe.advertised_capabilities else "unknown",
            cooperative_cancel="verified",
            approval_bridge="unsupported",
            read_only="unknown",
            patch_output="unknown",
            provider_sandbox="advertised" if "--sandbox" in self.command else "unknown",
        )
        decoded = decode_qwen_stream_json("\n".join(QWEN_OFFLINE_FIXTURE), returncode=0)
        failure = None if any(event[0] == "provider.usage" for event in decoded) else "Qwen Stream-JSON fixture did not produce usage"
        checks: list[object] = ["version/help probe", "Stream-JSON fixture", "unknown-event preservation", "process cancellation"]
        if live and failure is None:
            live_ok, live_summary = self._bounded_live_smoke()
            checks.append(live_summary)
            if not live_ok:
                failure = "Qwen live smoke test failed"
        return states, {"checks": checks, "fixture_hashes": [sha256_text(QWEN_OFFLINE_FIXTURE)]}, failure

    def _bounded_live_smoke(self) -> tuple[bool, str]:
        original_timeout = self.timeout
        self.timeout = min(self.timeout, 60)
        try:
            with tempfile.TemporaryDirectory(prefix="muxdev-qwen-cert-") as temp:
                output = self.run_stage(stage_id="certify", task="Reply exactly MUXDEV_CERT_OK without tools.", worktree=Path(temp))
                return output.returncode == 0, f"live smoke exit={output.returncode}"
        finally:
            self.timeout = original_timeout


class ReplayAdapter(ProviderAdapter):
    """Hash-verified fixture replay with no shell, network, or Provider calls."""

    id = "replay"
    adapter_version = f"replay/{ADAPTER_CONTRACT_VERSION}"
    trust_tier = TrustTier.MANAGED

    def __init__(self, fixture: Sequence[Mapping[str, object]], *, fixture_hash: str | None = None) -> None:
        canonical = json.dumps(list(fixture), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        actual_hash = sha256_text([canonical])
        if fixture_hash is not None and fixture_hash != actual_hash:
            raise ValueError("Replay fixture hash mismatch")
        self.fixture = tuple(dict(item) for item in fixture)
        self.fixture_hash = actual_hash

    @classmethod
    def from_registered_fixture(cls, path: Path, *, registry_root: Path, fixture_hash: str) -> "ReplayAdapter":
        resolved_root = registry_root.resolve()
        resolved = path.resolve()
        if resolved != resolved_root and resolved_root not in resolved.parents:
            raise ValueError("Replay fixture escapes the registered fixture directory")
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ValueError("Replay fixture must be a JSON array of events")
        return cls(payload, fixture_hash=fixture_hash)

    def probe(self) -> AdapterProbe:
        return AdapterProbe(
            provider=self.id,
            available=True,
            executable=None,
            provider_version="builtin",
            executable_fingerprint=self.fixture_hash,
            help_fingerprint="builtin-replay",
            adapter_version=self.adapter_version,
            platform="portable",
            advertised_capabilities=tuple(capability_map().keys()),
        )

    def certify(self, *, live: bool = False, acknowledged: bool = False, max_cost_usd: float | None = None) -> CertificationReport:
        require_live_acknowledgement(live=live, acknowledged=acknowledged, max_cost_usd=max_cost_usd)
        return make_certification_report(
            probe=self.probe(),
            capabilities=capability_map(
                structured_events="verified", tool_events="verified", usage="verified",
                session_resume="verified", cooperative_cancel="verified", approval_bridge="verified",
                read_only="verified", patch_output="verified", provider_sandbox="verified",
            ),
            trust_tier=self.trust_tier,
            live=live,
            evidence={"checks": ["fixture hash", "side-effect-free replay"], "fixture_hashes": [self.fixture_hash]},
        )

    def events(self, handle: AttemptHandle) -> Iterator[HarnessEvent]:
        decoded: list[tuple[str, HarnessEventSource, dict[str, object]]] = [
            ("harness.attempt_started", HarnessEventSource.HARNESS, {"simulated": True, "fixture_hash": self.fixture_hash})
        ]
        for item in self.fixture:
            event_type = str(item.get("event_type") or "provider.unknown")
            source_value = str(item.get("source") or "provider")
            try:
                source = HarnessEventSource(source_value)
            except ValueError:
                source = HarnessEventSource.PROVIDER
                event_type = "provider.unknown"
            payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {"event": dict(item)}
            decoded.append((event_type, source, dict(payload)))
        decoded.append(("harness.attempt_completed", HarnessEventSource.HARNESS, {"simulated": True, "returncode": 0}))
        events = _build_harness_events(
            run_id=str(handle.metadata.get("run_id") or "unbound"), stage_id=handle.stage_id,
            provider=self.id, attempt=int(handle.metadata.get("attempt") or 1), decoded=decoded,
        )
        review_result = ""
        if "review" in handle.stage_id.lower():
            review_result = '\n{"has_blockers": false, "blockers": []}\n'
        handle.metadata["output"] = ProviderStageOutput(
            artifact_name=f"replay/{handle.stage_id}.json",
            content="# SIMULATED REPLAY\nThis output came from a verified fixture and is not production delivery evidence.\n" + review_result,
            summary="verified fixture replay (simulated)", tokens=0, cost_usd=0, returncode=0,
            harness_events=tuple(events),
        )
        yield from events

    def run_stage(
        self, *, stage_id: str, task: str, worktree: Path,
        skills: list[dict[str, object]] | None = None, session_dir: Path | None = None,
        run_id: str | None = None, attempt: int = 1,
    ) -> ProviderStageOutput:
        handle = self.start(
            stage_id=stage_id, task=task, worktree=worktree, skills=skills or [],
            session_dir=session_dir, run_id=run_id, attempt=attempt,
        )
        tuple(self.events(handle))
        return handle.metadata["output"]  # type: ignore[return-value]

    def cancel(self, handle: AttemptHandle) -> CancelResult:
        return CancelResult(status="cancelled", cooperative=True)

    def resume(self, handle: AttemptHandle) -> AttemptHandle | Unsupported:
        return self.start(**handle.metadata)


def decode_codex_jsonl(output: str, *, returncode: int) -> list[tuple[str, HarnessEventSource, dict[str, object]]]:
    mapping = {
        "thread.started": "provider.session.started",
        "turn.started": "provider.turn.started",
        "turn.completed": "provider.turn.completed",
        "turn.failed": "provider.turn.failed",
        "item.started": "provider.item.started",
        "item.completed": "provider.item.completed",
        "error": "provider.error",
    }
    decoded = _decode_json_lines(output, mapping=mapping)
    expanded: list[tuple[str, HarnessEventSource, dict[str, object]]] = []
    for event_type, source, payload in decoded:
        expanded.append((event_type, source, payload))
        item = payload.get("item")
        if isinstance(item, Mapping) and str(item.get("type") or "") in {"command_execution", "mcp_tool_call", "file_change"}:
            expanded.append(("provider.tool", HarnessEventSource.PROVIDER, _compact_tool_payload(item)))
        usage = payload.get("usage")
        if isinstance(usage, Mapping):
            expanded.append(("provider.usage", HarnessEventSource.PROVIDER, {"usage": dict(usage)}))
    if not expanded:
        expanded.append(("provider.terminal", HarnessEventSource.DERIVED, {"returncode": returncode}))
    return expanded


def decode_qwen_stream_json(output: str, *, returncode: int) -> list[tuple[str, HarnessEventSource, dict[str, object]]]:
    mapping = {
        "system": "provider.session.started",
        "assistant": "provider.message.completed",
        "tool_use": "provider.tool.started",
        "tool_result": "provider.tool.completed",
        "result": "provider.turn.completed",
        "error": "provider.error",
    }
    decoded = _decode_json_lines(output, mapping=mapping)
    expanded: list[tuple[str, HarnessEventSource, dict[str, object]]] = []
    for event_type, source, payload in decoded:
        expanded.append((event_type, source, payload))
        usage = payload.get("usage")
        if isinstance(usage, Mapping):
            expanded.append(("provider.usage", HarnessEventSource.PROVIDER, {"usage": dict(usage)}))
    if not expanded:
        expanded.append(("provider.terminal", HarnessEventSource.DERIVED, {"returncode": returncode}))
    return expanded


def _decode_json_lines(output: str, *, mapping: Mapping[str, str]) -> list[tuple[str, HarnessEventSource, dict[str, object]]]:
    decoded: list[tuple[str, HarnessEventSource, dict[str, object]]] = []
    for line_number, line in enumerate(output.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            decoded.append(("provider.unknown", HarnessEventSource.PROVIDER, {"line": line_number, "reason": "invalid_json"}))
            continue
        if not isinstance(payload, dict):
            decoded.append(("provider.unknown", HarnessEventSource.PROVIDER, {"line": line_number, "event": payload}))
            continue
        raw_type = str(payload.get("type") or "")
        event_type = mapping.get(raw_type, "provider.unknown")
        compact = _compact_payload(payload)
        decoded.append((event_type, HarnessEventSource.PROVIDER, compact))
    return decoded


def _compact_payload(payload: Mapping[str, object]) -> dict[str, object]:
    allowed = {"type", "subtype", "thread_id", "session_id", "usage", "error", "status", "item", "tool_name", "tool_id"}
    result = {str(key): value for key, value in payload.items() if key in allowed}
    item = result.get("item")
    if isinstance(item, Mapping):
        result["item"] = {
            str(key): value
            for key, value in item.items()
            if key in {"id", "type", "status", "exit_code"}
        }
    return result


def _compact_tool_payload(payload: Mapping[str, object]) -> dict[str, object]:
    allowed = {"id", "type", "status", "exit_code", "command", "server", "tool", "path"}
    result = {str(key): value for key, value in payload.items() if key in allowed}
    if "command" in result:
        result["command"] = "<redacted-command>"
    return result


def _command_has(command: Sequence[str], flag: str, value: str | None = None) -> bool:
    if flag not in command:
        return False
    if value is None:
        return True
    index = command.index(flag)
    return index + 1 < len(command) and command[index + 1] == value


def _codex_sandbox_sentinel(probe: AdapterProbe) -> bool:
    """Exercise Codex's no-model sandbox subcommand with a harmless local command."""
    if not probe.executable:
        return False
    if os.name == "nt":
        provider_command = script_invocation(probe.executable, ("sandbox", "windows", "--full-auto", "--", "cmd", "/c", "echo", "muxdev-sandbox-sentinel"))
    elif platform.system().lower() == "linux":
        provider_command = [probe.executable, "sandbox", "linux", "--full-auto", "--", "sh", "-c", "printf muxdev-sandbox-sentinel"]
    else:
        return False
    try:
        completed = subprocess.run(
            provider_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            env=_static_probe_env(),
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and "muxdev-sandbox-sentinel" in (completed.stdout or "")
