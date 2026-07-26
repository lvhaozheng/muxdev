from __future__ import annotations

import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Mapping, Sequence

import pytest

from muxdev.runtime import agent_sessions
from muxdev.runtime.agent_session_startup import terminal_output_to_text
from muxdev.runtime.agent_sessions import (
    AgentSessionManager,
    LiveAgentSession,
    SessionLifecycleError,
)
from muxdev.runtime.terminal import ConPtyBackend, TerminalBackend, TerminalSnapshot
from muxdev.storage import ControlStore


class _ControlledPtyBackend(TerminalBackend):
    backend_name = "conpty"
    supports_resize = True

    def __init__(self) -> None:
        super().__init__()
        self.output: queue.Queue[str] = queue.Queue()
        self.spawned = threading.Event()
        self.argv: list[str] = []
        self.writes: list[str] = []
        self.running = False
        self.exit_code: int | None = None

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        cols: int = 120,
        rows: int = 32,
    ) -> None:
        self.argv = list(argv)
        self.cols, self.rows = cols, rows
        self.running = True
        self.spawned.set()

    def write(self, data: str) -> None:
        if not self.running:
            raise RuntimeError("terminal process is not running")
        self.writes.append(data)

    def read(self, *, timeout: float = 0.25) -> str:
        try:
            return self.output.get(timeout=timeout)
        except queue.Empty:
            return ""

    def snapshot(self) -> TerminalSnapshot:
        return TerminalSnapshot(
            self.backend_name,
            self.running,
            self.exit_code,
            self.cols,
            self.rows,
            False,
        )

    def close(self) -> None:
        self.running = False
        self.exit_code = 0


def _seed_conversation(workspace: Path, conversation_id: str) -> None:
    with ControlStore(workspace) as store:
        store.create_conversation(
            conversation_id=conversation_id,
            title="startup",
            goal="startup",
            status="working",
            metadata={},
            mode="direct",
            primary_agent_id="deepcode",
        )


def _manager_with_backend(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: _ControlledPtyBackend,
) -> AgentSessionManager:
    manager = AgentSessionManager(workspace)
    executable = str(workspace / "deepcode.exe")
    monkeypatch.setattr(
        manager.registry,
        "_resolve_adapter_executable",
        lambda _adapter: ("deepcode", executable),
    )
    monkeypatch.setattr(
        agent_sessions,
        "terminal_backend",
        lambda **_kwargs: backend,
    )
    return manager


def test_interactive_bootstrap_waits_for_ready_marker_and_submits_once(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = "conv_startup_ready"
    _seed_conversation(workspace, conversation_id)
    backend = _ControlledPtyBackend()
    manager = _manager_with_backend(workspace, monkeypatch, backend)
    bootstrap = '{\n  "goal": "build it"\n}'

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            manager.start,
            conversation_id=conversation_id,
            assignment_id=None,
            agent_id="deepcode",
            worktree=workspace,
            bootstrap=bootstrap,
        )
        assert backend.spawned.wait(timeout=1)
        backend.output.put("\x1b[36mDeep Code is starting\x1b[0m")
        time.sleep(0.1)
        assert backend.writes == []

        backend.output.put("\x1b[2mType your ")
        backend.output.put("message...\x1b[0m")
        session = future.result(timeout=2)

    assert session["status"] == "ready"
    assert backend.writes == [
        "\x1b[200~" + bootstrap + "\x1b[201~",
        "\r",
    ]
    events = manager.events(session["session_id"])
    bootstrap_events = [
        event
        for event in events
        if event["direction"] == "input"
        and event["metadata"].get("bootstrap") is True
    ]
    assert len(bootstrap_events) == 1
    assert bootstrap_events[0]["data"] == bootstrap + "\n"
    manager.close(session["session_id"])


def test_codex_bootstrap_uses_native_initial_prompt_instead_of_terminal_paste(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = "conv_codex_argv_bootstrap"
    _seed_conversation(workspace, conversation_id)
    backend = _ControlledPtyBackend()
    manager = AgentSessionManager(workspace)
    executable = str(workspace / "codex.exe")
    monkeypatch.setattr(
        manager.registry,
        "_resolve_adapter_executable",
        lambda _adapter: ("codex", executable),
    )
    monkeypatch.setattr(
        agent_sessions,
        "terminal_backend",
        lambda **_kwargs: backend,
    )
    bootstrap = '{"goal":"' + ("build the requested feature " * 80) + '"}'

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            manager.start,
            conversation_id=conversation_id,
            assignment_id=None,
            agent_id="codex",
            worktree=workspace,
            bootstrap=bootstrap,
        )
        assert backend.spawned.wait(timeout=1)
        backend.output.put("OpenAI Codex")
        session = future.result(timeout=3)

    assert session["status"] == "busy"
    assert backend.writes == []
    assert bootstrap not in backend.argv
    initial_prompt = backend.argv[-1]
    assert "Read and follow the complete MuxDev task brief at" in initial_prompt
    prompt_path = Path(str(session["metadata"]["bootstrap_path"]))
    assert prompt_path.read_text(encoding="utf-8").rstrip() == bootstrap
    assert session["metadata"]["bootstrap_transport"] == "argv"
    inputs = [
        event
        for event in manager.events(session["session_id"])
        if event["direction"] == "input"
    ]
    assert len(inputs) == 1
    assert inputs[0]["data"] == bootstrap + "\n"
    assert inputs[0]["metadata"]["transport"] == "argv"
    live = manager._live(session["session_id"])
    legacy_bootstrap = '{"goal":"recover the legacy terminal paste"}'
    manager._append(
        live,
        "input",
        legacy_bootstrap + "\n",
        metadata={"bootstrap": True},
    )
    manager._append(live, "output", "› [Pasted Content 1925 chars]\n")
    assert (
        manager._recover_unsubmitted_bootstrap(session["session_id"])
        == legacy_bootstrap
    )
    manager._append(live, "output", "• Working (1s • esc to interrupt)\n")
    assert manager._recover_unsubmitted_bootstrap(session["session_id"]) == ""
    manager.close(session["session_id"])


def test_interactive_startup_timeout_fails_instead_of_staying_running(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = "conv_startup_timeout"
    _seed_conversation(workspace, conversation_id)
    backend = _ControlledPtyBackend()
    manager = _manager_with_backend(workspace, monkeypatch, backend)
    manager.registry.adapters["deepcode"] = manager.registry.adapters[
        "deepcode"
    ].model_copy(update={"startup_timeout_seconds": 0.15})

    with pytest.raises(SessionLifecycleError) as raised:
        manager.start(
            conversation_id=conversation_id,
            assignment_id=None,
            agent_id="deepcode",
            worktree=workspace,
            bootstrap="do the work",
        )

    assert raised.value.code == "session_startup_timeout"
    with ControlStore(workspace) as store:
        sessions = store.list_agent_sessions(conversation_id)
    assert len(sessions) == 1
    assert sessions[0]["status"] == "failed"
    assert sessions[0]["metadata"]["startup_error_code"] == (
        "session_startup_timeout"
    )
    assert backend.writes == []


def test_ready_marker_followed_by_startup_dialog_never_dispatches(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = "conv_startup_dialog"
    _seed_conversation(workspace, conversation_id)
    backend = _ControlledPtyBackend()
    manager = _manager_with_backend(workspace, monkeypatch, backend)
    manager.registry.adapters["deepcode"] = manager.registry.adapters[
        "deepcode"
    ].model_copy(
        update={
            "startup_ready_pattern": r"Type your message\.\.\.",
            "startup_blocking_pattern": r"Press enter to continue",
            "startup_timeout_seconds": 0.25,
        }
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            manager.start,
            conversation_id=conversation_id,
            assignment_id=None,
            agent_id="deepcode",
            worktree=workspace,
            bootstrap="do the work",
        )
        assert backend.spawned.wait(timeout=1)
        backend.output.put("Type your message...")
        backend.output.put("Press enter to continue")
        with pytest.raises(SessionLifecycleError) as raised:
            future.result(timeout=2)

    assert raised.value.code == "session_startup_timeout"
    assert backend.writes == []


def test_conpty_close_force_terminates_a_cli_that_ignores_graceful_exit() -> None:
    class _StubbornProcess:
        def __init__(self) -> None:
            self.alive = True
            self.calls: list[bool] = []

        def isalive(self) -> bool:
            return self.alive

        def terminate(self, *, force: bool) -> None:
            self.calls.append(force)
            if force:
                self.alive = False

    backend = ConPtyBackend()
    process = _StubbornProcess()
    backend.process = process

    backend.close()

    assert process.calls == [False, True]
    assert process.alive is False


def test_terminal_output_to_text_removes_ansi_repaints_and_echoed_fragments() -> None:
    raw = (
        "\x1b[?2026h\x1b[38;2;246;226;183mAgent running\x1b[0m"
        "\x1b]0;private terminal title\x07"
        "\x1b[2;1H\x1b[KDone\r\n"
        "You have 2 usage limit resets available. Run /usage to use one.\r\n"
        "Would you like to run the following command?\r\n"
        "status: completed · tokens: 42\r\n"
        "[48;2;41;41;41m[K[39m[49m[0m[?25h"
    )

    rendered = terminal_output_to_text(raw)

    assert "Agent running" in rendered
    assert "Done" in rendered
    assert "private terminal title" not in rendered
    assert "usage limit resets" not in rendered
    assert "Would you like" not in rendered
    assert "status: completed" not in rendered
    assert "[38;2;" not in rendered
    assert "[48;2;" not in rendered
    assert "[?2026h" not in rendered
    assert "\x1b" not in rendered


def test_transcript_summary_uses_plain_output_only(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = AgentSessionManager(workspace)
    monkeypatch.setattr(
        manager,
        "events",
        lambda *_args, **_kwargs: [
            {"direction": "system", "data": '{"backend":"conpty"}'},
            {
                "direction": "output",
                "data": "\x1b[31mUseful result\x1b[0m[38;2;1;2;3m",
            },
        ],
    )

    summary = manager.transcript_summary("ses_summary")

    assert summary == "Useful result"


def test_deepcode_completion_pattern_handles_split_terminal_chunks(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = AgentSessionManager(workspace)
    live = LiveAgentSession(
        session_id="ses_deepcode_completed",
        backend=_ControlledPtyBackend(),
        transcript_path=workspace / "deepcode.jsonl",
        turn_completed_pattern=re.compile(
            r"(?im)^status:\s*completed(?:\s*[·•]\s*tokens:\s*\d+)?\s*$"
        ),
        turn_active=True,
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        manager,
        "transcript_summary",
        lambda *_args, **_kwargs: "DeepCode final answer",
    )
    monkeypatch.setattr(
        agent_sessions,
        "project_agent_turn_completed",
        lambda _workspace, session_id, summary: calls.append(
            (session_id, summary)
        )
        or True,
    )

    manager._project_turn_completion(live, "status: comp")
    manager._project_turn_completion(live, "leted · tokens: 42\n")

    assert calls == [
        ("ses_deepcode_completed", "DeepCode final answer")
    ]
    assert live.turn_active is False
