"""Serve an isolated local workspace for browser release checks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from muxdev.api import create_app
from muxdev.runtime import ConversationService, RunEngine
from muxdev.runtime.agent_sessions import agent_session_manager
from muxdev.runtime.collaboration_service import CollaborationService
from muxdev.storage import ControlStore


def seed_design_conversation(workspace: Path) -> None:
    """Create a deterministic reviewed turn for same-state visual QA."""
    conversation_id = "conv_design_conversation"
    run_id = "run_design_conversation"
    target = workspace / "src" / "checkout.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def retry_delay(attempt: int) -> float:\n"
        "    return min(0.25 * (2 ** attempt), 4.0)\n",
        encoding="utf-8",
    )
    with ControlStore(workspace) as store:
        if store.get_conversation(conversation_id):
            return
        store.create_conversation(
            conversation_id=conversation_id,
            title="Harden checkout retry handling",
            goal="Bound checkout retries and preserve review evidence.",
            status="awaiting_acceptance",
            metadata={"worktree": str(workspace)},
            mode="direct",
            primary_agent_id="codex",
        )
        contract = store.create_delivery_contract(
            conversation_id,
            goal="Bound checkout retries and preserve review evidence.",
            acceptance_criteria=[
                "Retry delay is bounded.",
                "Verification history remains visible.",
            ],
            allowed_scope=["src/checkout.py", "tests/"],
            workflow="change",
            profile="standard",
            provider="codex",
            max_cost_usd=0.5,
            policy={"delivery_standard": {"profile": "standard"}},
        )
        store.create_run(
            run_id=run_id,
            run_kind="delivery_verification",
            conversation_id=conversation_id,
            task="Bound checkout retries.",
            workflow="change",
            profile="standard",
            provider="codex",
            policy_hash="sha256:e2e-design-policy",
            metadata={"worktree": str(workspace)},
        )
        store.update_conversation(
            conversation_id,
            active_run_id=run_id,
            status="awaiting_acceptance",
        )
        store.record_file_baseline(
            conversation_id=conversation_id,
            run_id=run_id,
            path="src/checkout.py",
            existed=True,
            blob_hash=None,
            size=54,
        )
        store.record_file_change(
            conversation_id=conversation_id,
            run_id=run_id,
            path="src/checkout.py",
            kind="modify",
            before_hash="sha256:e2e-before",
            after_hash="sha256:e2e-after",
            patch=(
                "--- a/src/checkout.py\n"
                "+++ b/src/checkout.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def retry_delay(attempt: int) -> float:\n"
                "-    return 0.25 * (2 ** attempt)\n"
                "+    return min(0.25 * (2 ** attempt), 4.0)\n"
            ),
            additions=1,
            deletions=1,
            capture_grade="verified",
        )
        store.create_verification_attempt(
            conversation_id=conversation_id,
            run_id=run_id,
            command=["python", "-m", "pytest", "-q", "tests/test_checkout.py"],
            status="passed",
            exit_code=0,
            duration_ms=842,
            summary="12 checkout retry tests passed",
        )
        for event_type, payload, grade in (
            (
                "requirements.ready",
                {"summary": "Scope and acceptance criteria frozen"},
                "recorded",
            ),
            (
                "assignment.started",
                {"agent_id": "codex", "title": "Implement bounded retry delay"},
                "recorded",
            ),
            (
                "file.changed",
                {"path": "src/checkout.py", "kind": "modify"},
                "observed",
            ),
            (
                "workspace.reconciled",
                {"file_count": 1, "additions": 1, "deletions": 1},
                "verified",
            ),
        ):
            store.append_conversation_event(
                conversation_id,
                event_type,
                payload,
                actor="runtime" if grade != "recorded" else "codex",
                run_id=run_id,
                capture_grade=grade,
            )
        candidate = store.create_delivery_candidate(
            conversation_id,
            contract_id=str(contract["contract_id"]),
            run_id=run_id,
            status="verified",
            subject_digest="sha256:e2e-subject",
            changeset_digest="sha256:e2e-changes",
            message_sequence=4,
            policy_hash="sha256:e2e-design-policy",
            parent_candidate_id=None,
            evidence_path=None,
            metadata={"summary": "Bounded checkout retry delay; focused tests pass."},
        )
        store.update_conversation(
            conversation_id,
            active_candidate_id=str(candidate["candidate_id"]),
            status="awaiting_acceptance",
        )
        store.append_conversation_event(
            conversation_id,
            "delivery.candidate_ready",
            {
                "candidate_id": candidate["candidate_id"],
                "summary": "Bounded checkout retry delay; focused tests pass.",
            },
            actor="runtime",
            run_id=run_id,
            capture_grade="verified",
        )


def seed_action_conversation(
    workspace: Path,
    *,
    title: str,
    filename: str,
    keep_primary_session: bool = False,
) -> None:
    """Create a genuine Evidence v3 candidate for review action testing."""
    engine = RunEngine(workspace)
    service = CollaborationService(
        ConversationService(engine, engine.store),
        engine.store,
    )
    try:
        detail = service.create(
            f"Create {filename} with verified content.",
            title=title,
            mode="direct",
            agent_id="mock",
            acceptance_criteria=[f"{filename} contains verified content."],
            deliverables=[{"type": "code_change"}],
        )
        conversation_id = str(detail["conversation"]["conversation_id"])
        primary = detail["assignments"][0]
        (Path(primary["worktree"]) / filename).write_text(
            "verified browser fixture\n",
            encoding="utf-8",
        )
        service.report(
            str(primary["assignment_id"]),
            {
                "summary": f"Created {filename}.",
                "deliverables": [filename],
                "proof": ["verified changeset"],
            },
        )
        reviewer = next(
            item
            for item in service.store.list_assignments(conversation_id)
            if item["dispatch_kind"] == "review"
        )
        service.report(
            str(reviewer["assignment_id"]),
            {
                "summary": "Independent review passed.",
                "status": "passed",
                "deliverables": ["review"],
                "proof": ["review manifest"],
                "findings": [],
            },
        )
        manager = agent_session_manager(workspace)
        for session in service.store.list_agent_sessions(conversation_id):
            if (
                keep_primary_session
                and session["agent_id"] == "mock"
                and session["lane_key"] == "main"
            ):
                continue
            manager.close(str(session["session_id"]))
    finally:
        engine.store.close()


def seed_failed_conversation(workspace: Path) -> None:
    """Create a failed Session whose transcript remains readable and restartable."""
    engine = RunEngine(workspace)
    service = CollaborationService(
        ConversationService(engine, engine.store),
        engine.store,
    )
    try:
        detail = service.create(
            "Inspect README.md after a simulated process failure.",
            title="Restart failed Agent Session",
            mode="direct",
            agent_id="mock",
            deliverables=[{"type": "answer"}],
        )
        conversation_id = str(detail["conversation"]["conversation_id"])
        assignment = detail["assignments"][0]
        run = detail["runs"][0]
        session = detail["sessions"][0]
        manager = agent_session_manager(workspace)
        manager.close(str(session["session_id"]))
        error = {
            "code": "session_failed",
            "message": "Simulated Agent process exit for browser recovery testing.",
            "remediation": "查看失败输出后点击“重新启动”。",
            "retryable": True,
        }
        service.store.update_agent_session(
            str(session["session_id"]),
            status="failed",
            current_assignment_id=str(assignment["assignment_id"]),
            metadata={"launch_error": error["message"]},
        )
        service.store.update_assignment(
            str(assignment["assignment_id"]),
            status="failed",
            metadata={"failure": error["message"]},
        )
        service.store.update_run(
            str(run["run_id"]),
            status="failed",
            current_stage=None,
        )
        service.store.update_conversation(
            conversation_id,
            status="needs_user",
            active_run_id=str(run["run_id"]),
            metadata={"startup_error": error},
        )
        service.store.append_conversation_event(
            conversation_id,
            "run.failed_to_start",
            {
                "assignment_id": assignment["assignment_id"],
                "run_id": run["run_id"],
                "session_id": session["session_id"],
                "reason": error["message"],
                "error": error,
            },
            actor="runtime",
            run_id=str(run["run_id"]),
            assignment_id=str(assignment["assignment_id"]),
            session_id=str(session["session_id"]),
            generation=int(session["generation"]),
        )
    finally:
        engine.store.close()


def main() -> None:
    workspace = Path(
        os.environ.get(
            "MUXDEV_E2E_WORKSPACE",
            f".test_workspaces/browser-e2e-{os.getpid()}",
        )
    ).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    readme = workspace / "README.md"
    if not readme.exists():
        readme.write_text("browser e2e fixture\n", encoding="utf-8")
    seed_design_conversation(workspace)
    seed_action_conversation(
        workspace,
        title="Accept verified browser delivery",
        filename="accept-browser.txt",
    )
    seed_action_conversation(
        workspace,
        title="Revise verified browser delivery",
        filename="revise-browser.txt",
        keep_primary_session=True,
    )
    seed_action_conversation(
        workspace,
        title="Rollback verified browser delivery",
        filename="rollback-browser.txt",
    )
    seed_failed_conversation(workspace)
    uvicorn.run(
        create_app(workspace),
        host="127.0.0.1",
        port=int(os.environ.get("MUXDEV_E2E_PORT", "18977")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
