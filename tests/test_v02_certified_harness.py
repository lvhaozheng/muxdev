from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from muxdev.api.web import create_app
from muxdev.clients.sessions.backends import DockerBackend, SessionResult, _provider_subprocess_env
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.domain import CancellationToken, HarnessPolicySpec, RunSpec
from muxdev.models import ApprovalStatus, RunStatus
from muxdev.providers import (
    CapabilityVerificationState,
    CertificationStatus,
    HarnessEvent,
    HarnessEventSource,
    MockProviderAdapter,
    ReplayAdapter,
    TrustTier,
    decode_codex_jsonl,
    decode_qwen_stream_json,
    get_runtime_provider,
)
from muxdev.providers import certification as certification_module
from muxdev.providers.certification import certification_is_current, make_certification_report
from muxdev.providers.certified import CodexHarnessAdapter
from muxdev.providers.harness import AdapterProbe, capability_map
from muxdev.providers.policy import ensure_harness_policy
from muxdev.runtime import SupervisorRuntime
from muxdev.runtime import supervisor as supervisor_module
from muxdev.storage import Blackboard


@pytest.fixture
def workspace() -> Iterator[Path]:
    root = Path(".test_workspaces") / f"v02_harness_{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root.resolve()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _board(workspace: Path, *, sink=None) -> Blackboard:
    run_dir = workspace / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    board = Blackboard(run_dir, event_sink=sink)
    board.create_run(
        run_id="run-1",
        task="test",
        workflow="software-dev",
        provider="mock",
        workspace=workspace,
        worktree=workspace,
    )
    return board


class OfflineOpaqueAdapter:
    id = "opaque"
    adapter_version = "opaque-test/1"
    trust_tier = TrustTier.OPAQUE
    isolation_mode = "provider_sandbox"

    def __init__(self, *, fingerprint: str = "binary-a") -> None:
        self.fingerprint = fingerprint
        self.started = 0

    def probe(self) -> AdapterProbe:
        return AdapterProbe(
            provider=self.id,
            available=True,
            executable="opaque",
            provider_version="1.0",
            executable_fingerprint=self.fingerprint,
            help_fingerprint="help-a",
            adapter_version=self.adapter_version,
            platform="test",
            advertised_capabilities=("structured_events", "provider_sandbox", "cooperative_cancel"),
        )

    def certify(self, **_: object):
        return make_certification_report(
            probe=self.probe(),
            capabilities=capability_map(
                structured_events="verified",
                provider_sandbox="verified",
                cooperative_cancel="verified",
                approval_bridge="unsupported",
            ),
            trust_tier=self.trust_tier,
            live=False,
            evidence={"checks": ["fixture"]},
        )


def test_codex_decoder_maps_tools_usage_unknown_and_damage() -> None:
    rows = decode_codex_jsonl(
        "\n".join(
            [
                '{"type":"thread.started","thread_id":"t"}',
                '{"type":"item.completed","item":{"type":"command_execution","command":"secret command","exit_code":0}}',
                '{"type":"turn.completed","usage":{"input_tokens":2,"output_tokens":1}}',
                '{"type":"future.event","value":1}',
                "{damaged",
            ]
        ),
        returncode=0,
    )
    event_types = [row[0] for row in rows]
    assert "provider.session.started" in event_types
    assert "provider.tool" in event_types
    assert "provider.usage" in event_types
    assert event_types.count("provider.unknown") == 2
    tool = next(payload for event_type, _, payload in rows if event_type == "provider.tool")
    assert "command" not in tool


def test_qwen_decoder_maps_stream_json_and_preserves_unknown() -> None:
    rows = decode_qwen_stream_json(
        "\n".join(
            [
                '{"type":"system","subtype":"init","session_id":"s"}',
                '{"type":"tool_use","tool_name":"read_file","tool_id":"1"}',
                '{"type":"tool_result","tool_id":"1","status":"success"}',
                '{"type":"result","usage":{"input_tokens":2,"output_tokens":1}}',
                '{"type":"new_kind"}',
            ]
        ),
        returncode=0,
    )
    assert [row[0] for row in rows] == [
        "provider.session.started",
        "provider.tool.started",
        "provider.tool.completed",
        "provider.turn.completed",
        "provider.usage",
        "provider.unknown",
    ]


def test_mock_and_replay_are_managed_but_real_adapters_are_opaque(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: name)
    assert MockProviderAdapter().trust_tier == TrustTier.MANAGED
    assert ReplayAdapter([]).trust_tier == TrustTier.MANAGED
    assert get_runtime_provider("codex").trust_tier == TrustTier.OPAQUE
    assert get_runtime_provider("qwen").trust_tier == TrustTier.OPAQUE


def test_mock_certification_verifies_only_contract_capabilities() -> None:
    report = MockProviderAdapter().certify()
    assert report.status == CertificationStatus.OFFLINE_VERIFIED
    assert set(report.capabilities) == set(capability_map())
    assert all(state == CapabilityVerificationState.VERIFIED for state in report.capabilities.values())


def test_live_certification_requires_acknowledgement_and_budget() -> None:
    adapter = MockProviderAdapter()
    with pytest.raises(ValueError, match="--yes"):
        adapter.certify(live=True, max_cost_usd=0.1)
    with pytest.raises(ValueError, match="max-cost-usd"):
        adapter.certify(live=True, acknowledged=True)


def test_certification_stales_on_bound_fingerprint_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = OfflineOpaqueAdapter().probe()
    report = make_certification_report(
        probe=probe,
        capabilities=capability_map(structured_events="verified"),
        trust_tier=TrustTier.OPAQUE,
        live=False,
        evidence={"checks": ["fixture"]},
    ).to_dict()
    assert certification_is_current(report, probe)
    for changed in (
        replace(probe, provider_version="2.0"),
        replace(probe, executable_fingerprint="binary-b"),
        replace(probe, help_fingerprint="help-b"),
        replace(probe, platform="other-platform"),
        replace(probe, adapter_version="opaque-test/2"),
    ):
        assert not certification_is_current(report, changed)
    monkeypatch.setattr(certification_module, "ADAPTER_POLICY_VERSION", "changed-policy")
    assert not certification_is_current(report, probe)


def test_codex_offline_certification_never_invokes_exec_or_model(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = workspace / "codex.exe"
    executable.write_bytes(b"fixture executable")
    commands: list[list[str]] = []
    environments: list[dict[str, str]] = []

    def fake_run(command, **kwargs):
        command = [str(item) for item in command]
        commands.append(command)
        environments.append(dict(kwargs.get("env") or {}))
        if "--version" in command:
            output = "codex-cli 0.144.1\n"
        elif "sandbox" in command:
            output = "muxdev-sandbox-sentinel\n"
        else:
            output = "--json --sandbox resume\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = CodexHarnessAdapter(
        [str(executable), "--ask-for-approval", "never", "exec", "--json", "--sandbox", "workspace-write"],
        prompt_transport="stdin",
    )
    report = adapter.certify(live=False)
    assert report.status == CertificationStatus.OFFLINE_VERIFIED
    assert all("exec" not in command[1:] for command in commands)
    assert all("OPENAI_API_KEY" not in env for env in environments)


def test_replay_hash_check_and_simulation_label(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = [{"event_type": "provider.tool.completed", "payload": {"tool": "fixture"}}]
    adapter = ReplayAdapter(fixture)
    with pytest.raises(ValueError, match="hash mismatch"):
        ReplayAdapter(fixture, fixture_hash="bad")
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("shell called")))
    output = adapter.run_stage(stage_id="demo", task="ignored", worktree=workspace, run_id="run-demo")
    assert "SIMULATED REPLAY" in output.content
    assert "not production delivery evidence" in output.content
    assert [event.sequence for event in output.harness_events] == list(range(1, len(output.harness_events) + 1))


def test_replay_registered_fixture_rejects_path_escape(workspace: Path) -> None:
    registry = workspace / "registry"
    registry.mkdir()
    outside = workspace / "outside.json"
    outside.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        ReplayAdapter.from_registered_fixture(outside, registry_root=registry, fixture_hash="unused")


def test_harness_event_hash_chain_idempotency_and_unknown_source(workspace: Path) -> None:
    board = _board(workspace)
    try:
        first = HarnessEvent.create(
            run_id="run-1", stage_id="code", provider="mock", attempt=1, sequence=1,
            event_type="provider.unknown", source=HarnessEventSource.PROVIDER,
            idempotency_key="unknown-1", payload={"raw": "OPENAI_API_KEY=sk-secret-value"},
        )
        second = HarnessEvent.create(
            run_id="run-1", stage_id="code", provider="mock", attempt=1, sequence=2,
            event_type="harness.attempt_completed", source=HarnessEventSource.HARNESS,
            idempotency_key="done-1", payload={"returncode": 0}, prev_hash=first.event_hash,
        )
        assert len(board.append_harness_events([first, second])) == 2
        assert board.append_harness_events([first, second]) == []
        stored = board.list_harness_events("run-1")
        assert all(event.verify_hash() for event in stored)
        assert stored[1].prev_hash == stored[0].event_hash
        assert "sk-secret-value" not in json.dumps(stored[0].payload)
    finally:
        board.close()


def test_harness_event_and_attempt_projection_roll_back_together(workspace: Path) -> None:
    board = _board(workspace)
    try:
        board.start_provider_attempt("run-1", "code", provider="mock", role="code", attempt=1)
        event = HarnessEvent.create(
            run_id="run-1", stage_id="code", provider="mock", attempt=1, sequence=1,
            event_type="harness.attempt_completed", source=HarnessEventSource.HARNESS,
            idempotency_key="rollback", payload={"returncode": 0},
        )
        board.engine.commit_validator = lambda: (_ for _ in ()).throw(RuntimeError("injected commit failure"))
        with pytest.raises(RuntimeError, match="injected"):
            board.complete_provider_attempt(
                "run-1", "code", provider="mock", attempt=1, status="succeeded", harness_events=[event]
            )
        board.engine.commit_validator = None
        assert board.list_harness_events("run-1") == []
        attempt = board.table_rows("provider_attempts", run_id="run-1")[0]
        assert attempt["status"] == "running"
    finally:
        board.close()


def test_websocket_sink_only_receives_committed_harness_events(workspace: Path) -> None:
    messages: list[dict[str, object]] = []
    board = _board(workspace, sink=messages.append)
    try:
        messages.clear()
        event = HarnessEvent.create(
            run_id="run-1", stage_id="code", provider="mock", attempt=1, sequence=1,
            event_type="provider.tool.completed", source=HarnessEventSource.PROVIDER,
            idempotency_key="commit-only", payload={"status": "ok"},
        )
        board.engine.commit_validator = lambda: (_ for _ in ()).throw(RuntimeError("rollback"))
        with pytest.raises(RuntimeError):
            board.append_harness_events([event])
        assert not any(message.get("type") == "harness_event" for message in messages)
        board.engine.commit_validator = None
        board.append_harness_events([event])
        assert [message["type"] for message in messages] == ["harness_event"]
    finally:
        board.close()


def test_scheduler_records_cooperative_or_forced_provider_cancellation(workspace: Path) -> None:
    board = _board(workspace)
    token = CancellationToken()
    token.bind_provider_attempt(run_id="run-1", stage_id="code", provider="mock", attempt=1)
    token.request_cancel("operator stop")
    token.record_process_cancellation(cooperative=False, forced=True)
    try:
        board.start_provider_attempt("run-1", "code", provider="mock", role="code", attempt=1)
        event = board.record_harness_cancellation("run-1", token.cancellation_context())
        assert event and event.event_type == "harness.attempt_cancelled"
        assert event.payload["forced"] is True
        attempt = board.table_rows("provider_attempts", run_id="run-1")[0]
        assert attempt["status"] == "cancelled"
        assert attempt["cancellation_mode"] == "forced"
    finally:
        board.close()


def test_v4_migration_adds_certification_events_and_attempt_metadata(workspace: Path) -> None:
    board = _board(workspace)
    try:
        assert board.storage_health()["schema_version"] == 7
        tables = {row[0] for row in board.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"adapter_certifications", "harness_events"} <= tables
        columns = {row[1] for row in board.conn.execute("PRAGMA table_info(provider_attempts)")}
        assert {"certification_id", "adapter_version", "trust_tier", "isolation_mode", "session_id", "waiver_approval_id", "cancellation_mode"} <= columns
    finally:
        board.close()


def test_runspec_v3_derives_high_risk_and_imports_v1_conservatively(workspace: Path) -> None:
    spec = RunSpec.from_submit_payload(task="migrate payment secrets", workspace=workspace, provider="codex")
    assert spec.to_payload()["schema_version"] == 4
    assert spec.harness_policy.risk_level == "high"
    assert spec.harness_policy.minimum_certification == "live_verified"
    legacy = dict(spec.to_payload())
    legacy["schema_version"] = 1
    legacy.pop("harness_policy")
    imported = RunSpec.from_payload(legacy)
    assert imported.harness_policy.minimum_certification == "offline_verified"
    assert "provider_sandbox" in imported.harness_policy.required_capabilities


def test_high_risk_missing_live_certification_pauses_before_provider_and_waiver_is_bound(workspace: Path) -> None:
    board = _board(workspace)
    adapter = OfflineOpaqueAdapter()
    policy = HarnessPolicySpec.derive(task="change payment auth", gate="strict", automation={})
    try:
        pending = ensure_harness_policy(board, run_id="run-1", adapters={"opaque": adapter}, policy=policy)
        assert pending.status == "awaiting_approval"
        assert adapter.started == 0
        approval = board.find_approval("run-1", None, "isolation_downgrade")
        assert approval and approval["status"] == str(ApprovalStatus.PENDING)
        board.decide_approval(str(approval["approval_id"]), ApprovalStatus.APPROVED)
        ready = ensure_harness_policy(board, run_id="run-1", adapters={"opaque": adapter}, policy=policy)
        assert ready.status == "ready"
        assert adapter.waiver_approval_id == approval["approval_id"]
    finally:
        board.close()


def test_waiver_becomes_stale_when_adapter_subject_changes(workspace: Path) -> None:
    board = _board(workspace)
    policy = HarnessPolicySpec.derive(task="change security permissions", gate="strict", automation={})
    first = OfflineOpaqueAdapter(fingerprint="binary-a")
    try:
        pending = ensure_harness_policy(board, run_id="run-1", adapters={"opaque": first}, policy=policy)
        board.decide_approval(str(pending.approval_id), ApprovalStatus.APPROVED)
        changed = OfflineOpaqueAdapter(fingerprint="binary-b")
        changed.adapter_version = "opaque-test/2"
        next_result = ensure_harness_policy(board, run_id="run-1", adapters={"opaque": changed}, policy=policy)
        assert next_result.status == "awaiting_approval"
        assert next_result.approval_id != pending.approval_id
    finally:
        board.close()


def test_normal_uncertified_adapter_fails_closed_without_downgrade(workspace: Path) -> None:
    board = _board(workspace)
    adapter = OfflineOpaqueAdapter()
    adapter.certify = lambda **kwargs: make_certification_report(
        probe=adapter.probe(), capabilities=capability_map(), trust_tier=TrustTier.OPAQUE,
        live=False, evidence={}, failure="fixture failure",
    )
    try:
        result = ensure_harness_policy(
            board, run_id="run-1", adapters={"opaque": adapter}, policy=HarnessPolicySpec()
        )
        assert result.status == "blocked"
        assert board.find_approval("run-1", None, "isolation_downgrade") is None
    finally:
        board.close()


def test_normal_certified_native_sandbox_is_ready_without_waiver(workspace: Path) -> None:
    board = _board(workspace)
    try:
        result = ensure_harness_policy(
            board,
            run_id="run-1",
            adapters={"opaque": OfflineOpaqueAdapter()},
            policy=HarnessPolicySpec(),
        )
        assert result.status == "ready"
        assert board.find_approval("run-1", None, "isolation_downgrade") is None
    finally:
        board.close()


def test_supervisor_high_risk_gate_stops_before_adapter_start(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OfflineOpaqueAdapter()
    monkeypatch.setattr(supervisor_module, "get_runtime_provider", lambda _: adapter)
    result = SupervisorRuntime(workspace, write_dashboards=False).run(
        "change payment authentication",
        provider="opaque",
        gate="strict",
    )
    assert result.status == RunStatus.AWAITING_APPROVAL
    assert adapter.started == 0
    with Blackboard(result.run_dir) as board:
        approval = board.find_approval(result.run_id, None, "isolation_downgrade")
        assert approval and approval["status"] == str(ApprovalStatus.PENDING)


def test_provider_environment_is_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUXDEV_SECRET", "must-not-leak")
    monkeypatch.setenv("PATH", "safe-path")
    env = _provider_subprocess_env({"CODEX_HOME": "private-state", "UNSAFE_OVERRIDE": "bad"})
    assert env["PATH"] == "safe-path"
    assert env["CODEX_HOME"] == "private-state"
    assert "MUXDEV_SECRET" not in env
    assert "UNSAFE_OVERRIDE" not in env


def test_docker_backend_uses_explicit_hardening_and_network_default(workspace: Path) -> None:
    backend = DockerBackend(docker="docker")
    captured: dict[str, object] = {}

    class FakeHeadless:
        def run(self, command, **kwargs):
            captured["command"] = command
            return SessionResult(0, "", "", [])

    backend.headless = FakeHeadless()
    backend.run(["echo", "ok"], cwd=workspace)
    command = captured["command"]
    assert isinstance(command, list)
    for flag in ("--read-only", "--cap-drop", "--security-opt", "--pids-limit", "--memory", "--cpus"):
        assert flag in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"


def test_http_exposes_only_readonly_harness_surfaces(workspace: Path) -> None:
    paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
    manager = TaskManager(paths=paths)
    try:
        with manager.board() as board:
            board.create_run(
                run_id="run-http", task="test", workflow="software-dev", provider="mock",
                workspace=workspace, worktree=workspace,
            )
            board.record_adapter_certification(MockProviderAdapter().certify())
            event = HarnessEvent.create(
                run_id="run-http", stage_id="code", provider="mock", attempt=1, sequence=1,
                event_type="provider.tool.completed", source=HarnessEventSource.PROVIDER,
                idempotency_key="http-event", payload={"token": "sk-secret-value"},
            )
            board.append_harness_events([event])
        with TestClient(create_app(task_manager=manager, paths=paths)) as client:
            assert client.get("/api/providers/certifications").status_code == 200
            assert client.get("/api/providers/mock/certification").json()["provider"] == "mock"
            events = client.get("/api/tasks/run-http/harness-events?path=../../secret").json()["events"]
            assert events and "sk-secret-value" not in json.dumps(events)
            assert client.get("/api/tasks/run-http/isolation").status_code == 200
            assert client.post("/api/providers/mock/certify", json={"live": True}).status_code == 404
            assert client.post("/api/providers/mock/command", json={"command": "whoami"}).status_code == 404
    finally:
        manager.close(timeout=5)


@pytest.mark.integration
@pytest.mark.parametrize("provider,expected_version", [("codex", "0.144.1"), ("qwen", "0.18.4")])
def test_installed_primary_adapters_pass_offline_certification(provider: str, expected_version: str) -> None:
    if shutil.which(provider) is None:
        pytest.skip(f"{provider} is not installed")
    report = get_runtime_provider(provider).certify(live=False)
    assert report.status == CertificationStatus.OFFLINE_VERIFIED
    assert expected_version in str(report.provider_version)
    assert report.trust_tier == TrustTier.OPAQUE
    assert report.capabilities["approval_bridge"] == CapabilityVerificationState.UNSUPPORTED
