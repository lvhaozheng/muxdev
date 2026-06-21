"""Interactive REPL command handling for muxdev."""

from __future__ import annotations

from pathlib import Path

from ..models import ApprovalStatus
from ..providers import detect_providers, probe_provider
from ..runtime import SupervisorRuntime
from ..core.safety import SafetyPolicyEngine
from ..services.skills import SkillRegistry
from ..storage import Blackboard, RunStore


def handle_repl_command(line: str, workspace: Path) -> tuple[bool, str]:
    """Execute one REPL command and return whether the loop should continue."""
    text = line.strip()
    if not text:
        return True, ""
    if text in {"/exit", "/quit"}:
        return False, "bye"
    if text == "/help":
        return True, (
            "commands: /status /providers /provider doctor NAME /agents /skills "
            "/skill install NAME /usage /approvals /approve ID /deny ID /run TASK /report [RUN] !shell /exit"
        )
    if text == "/providers":
        ready = [probe.provider for probe in detect_providers() if probe.status != "unavailable"]
        return True, "providers: " + ", ".join(ready)
    if text.startswith("/provider doctor "):
        probe = probe_provider(text.split(maxsplit=2)[2])
        return True, "\n".join(f"{key}: {value}" for key, value in probe.to_dict().items())
    if text == "/agents":
        rows = _latest_rows(workspace, "agents")
        if not rows:
            return True, "no agents recorded"
        return True, "\n".join(f"{row['role']}: {row['provider']} ({row['status']})" for row in rows)
    if text == "/skills":
        rows = SkillRegistry(workspace).list()
        if not rows:
            return True, "no skills installed"
        return True, "\n".join(f"{row.name}: {row.path}" for row in rows)
    if text.startswith("/skill install "):
        record = SkillRegistry(workspace).install(text.split(maxsplit=2)[2], native=True, provider="generic")
        return True, f"installed skill {record.name}: {record.path}"
    if text == "/usage":
        rows = _latest_rows(workspace, "usage_records")
        total = sum(float(row["cost_usd"]) for row in rows)
        tokens = sum(int(row["tokens"]) for row in rows)
        return True, f"tokens={tokens} cost_usd={total:.4f}"
    if text.startswith("!"):
        result = SafetyPolicyEngine().evaluate_shell(text[1:].strip())
        return True, f"{result.decision}: {result.reason}"
    if text == "/status":
        run_id = RunStore(workspace).latest_run_id()
        if not run_id:
            return True, "no runs"
        run_dir = RunStore(workspace).find_run_dir(run_id)
        blackboard = Blackboard(run_dir)
        try:
            run = blackboard.get_run(run_id)
            return True, f"{run_id}: {run['status']} ({run['provider']})"
        finally:
            blackboard.close()
    if text == "/approvals":
        rows = _approvals(workspace, status="pending")
        if not rows:
            return True, "no pending approvals"
        return True, "\n".join(f"{row['approval_id']} {row['type']} {row['reason']}" for row in rows)
    if text.startswith("/approve "):
        return True, _decide(workspace, text.split(maxsplit=1)[1], ApprovalStatus.APPROVED)
    if text.startswith("/deny "):
        return True, _decide(workspace, text.split(maxsplit=1)[1], ApprovalStatus.DENIED)
    if text.startswith("/run "):
        result = SupervisorRuntime(workspace).run(text.split(maxsplit=1)[1], provider="mock")
        return True, f"{result.run_id}: {result.status}"
    if text.startswith("/report"):
        parts = text.split(maxsplit=1)
        run_id = parts[1] if len(parts) == 2 else RunStore(workspace).latest_run_id()
        if not run_id:
            return True, "no runs"
        path = RunStore(workspace).find_run_dir(run_id) / "final_report.md"
        return True, path.read_text(encoding="utf-8") if path.exists() else f"report not found: {run_id}"
    return True, "unknown command; try /help"


def _latest_rows(workspace: Path, table: str) -> list[dict[str, object]]:
    run_id = RunStore(workspace).latest_run_id()
    if not run_id:
        return []
    blackboard = Blackboard(RunStore(workspace).find_run_dir(run_id))
    try:
        return blackboard.table_rows(table)
    finally:
        blackboard.close()


def start_repl(workspace: Path) -> None:
    """Run the prompt_toolkit REPL loop."""
    try:
        from prompt_toolkit import PromptSession
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("prompt_toolkit is required for muxdev repl") from exc

    session = PromptSession("muxdev> ")
    print("muxdev repl. Type /help or /exit.")
    running = True
    while running:
        try:
            line = session.prompt()
        except (EOFError, KeyboardInterrupt):
            print("bye")
            break
        running, message = handle_repl_command(line, workspace)
        if message:
            print(message)


def _approvals(workspace: Path, *, status: str | None = None) -> list[dict[str, object]]:
    store = RunStore(workspace)
    if not store.runs_dir.exists():
        return []
    rows: list[dict[str, object]] = []
    for run_dir in store.runs_dir.iterdir():
        if not (run_dir / "blackboard.sqlite").exists():
            continue
        blackboard = Blackboard(run_dir)
        try:
            rows.extend(blackboard.list_approvals(status=status))
        finally:
            blackboard.close()
    return rows


def _decide(workspace: Path, approval_id: str, status: ApprovalStatus) -> str:
    store = RunStore(workspace)
    run_exists = False
    for run_dir in store.runs_dir.iterdir() if store.runs_dir.exists() else []:
        if not (run_dir / "blackboard.sqlite").exists():
            continue
        run_exists = run_exists or run_dir.name == approval_id
        blackboard = Blackboard(run_dir)
        try:
            rows = blackboard.list_approvals(run_id=run_dir.name)
            try:
                resolved = _resolve_local_approval_id(rows, approval_id)
            except ValueError as exc:
                return str(exc)
            if resolved:
                blackboard.decide_approval(resolved, status)
                return f"{resolved}: {status}"
        finally:
            blackboard.close()
    if run_exists:
        return f"no pending approval for run: {approval_id}; use /run or recover the task"
    return f"approval not found: {approval_id}"


def _resolve_local_approval_id(rows: list[dict[str, object]], approval_or_run_id: str) -> str | None:
    for row in rows:
        if str(row.get("approval_id") or "") == approval_or_run_id:
            return str(row["approval_id"])
    pending_for_run = [
        row
        for row in rows
        if str(row.get("run_id") or row.get("task_id") or "") == approval_or_run_id
        and str(row.get("status") or "") == str(ApprovalStatus.PENDING)
    ]
    if len(pending_for_run) == 1:
        return str(pending_for_run[0]["approval_id"])
    if len(pending_for_run) > 1:
        raise ValueError(f"multiple pending approvals for run: {approval_or_run_id}; use an approval id")
    return None
