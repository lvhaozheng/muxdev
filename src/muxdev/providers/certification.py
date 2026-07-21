"""Fingerprint-bound offline and live Provider certification."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..config.loader import load_config
from ..core.platforms import hidden_subprocess_kwargs
from ..core.processes import ProcessSupervisor
from ..domain import CapabilityGrant, McpServerGrant, StageExecutionInput
from ..models import ChangeResult
from ..models.evidence import canonical_hash
from .adapters import get_runtime_provider
from .capabilities import provider_capabilities


def provider_fingerprint(workspace: Path, provider: str) -> dict[str, object]:
    definition = _definition(workspace, provider)
    runtime = definition.get("runtime") if isinstance(definition.get("runtime"), dict) else {}
    if str(runtime.get("kind")) == "mock":
        return {
            "provider": provider,
            "executable": "builtin:mock",
            "version": "builtin",
            "binary_digest": canonical_hash("muxdev.mock"),
            "command_template_digest": canonical_hash(runtime),
            "fingerprint": canonical_hash({"provider": provider, "runtime": runtime}),
        }
    template = [str(item) for item in runtime.get("command", [])]
    candidates = [*(template[:1]), *(str(item) for item in definition.get("commands", []))]
    executable = next((shutil.which(item) for item in candidates if shutil.which(item)), None)
    binary_digest = _file_digest(Path(executable)) if executable and Path(executable).is_file() else "unavailable"
    version = "reported-by-acp-initialize" if str(runtime.get("kind")) == "acp" else _version(executable)
    fact = {
        "provider": provider,
        "executable": str(Path(executable).resolve()) if executable else None,
        "version": version,
        "binary_digest": binary_digest,
        "command_template_digest": canonical_hash(runtime),
    }
    return {**fact, "fingerprint": canonical_hash(fact)}


def certify_provider(workspace: Path, provider: str, *, live: bool) -> dict[str, Any]:
    definition = _definition(workspace, provider)
    fingerprint = provider_fingerprint(workspace, provider)
    capabilities = provider_capabilities(workspace, provider)
    if provider == "mock":
        return {
            "provider": provider,
            "status": "live_verified",
            "live": True,
            "fingerprint": fingerprint,
            "capabilities": capabilities,
            "checks": {"builtin": True},
        }
    if not fingerprint.get("executable"):
        return {
            "provider": provider,
            "status": "failed",
            "live": live,
            "fingerprint": fingerprint,
            "capabilities": capabilities,
            "checks": {"installed": False},
        }
    if not live:
        return {
            "provider": provider,
            "status": "offline_verified",
            "live": False,
            "fingerprint": fingerprint,
            "capabilities": capabilities,
            "checks": {"installed": True, "live_schema": False},
        }
    grant = CapabilityGrant()
    if capabilities.get("mcp_config"):
        fixture = McpServerGrant(
            name="muxdev-certification",
            command=sys.executable,
            args=(str(Path(__file__).with_name("mcp_fixture.py")),),
            tools=("ping",),
        )
        grant = CapabilityGrant(
            read_workspace=True,
            mcp_tools=("muxdev-certification/ping",),
            mcp_servers=(fixture,),
        )
    checks: dict[str, object] = {"installed": True}
    try:
        adapter = get_runtime_provider(provider, workspace=workspace, definition=definition)
        with tempfile.TemporaryDirectory(prefix="muxdev-certify-") as temporary:
            worktree = Path(temporary)
            stage_input = StageExecutionInput(
                run_id=f"certify-{provider}",
                stage_id="certify",
                role="code",
                task=(
                    "Perform a minimal read-only handshake. If the muxdev-certification MCP server is available, "
                    "call its ping tool, then return JSON matching ChangeResult with no affected paths."
                ),
                worktree=worktree,
                context={},
                capabilities=grant,
                provider=provider,
                policy={"output_schema": "ChangeResult", "timeout_seconds": 60},
            )
            result = adapter.execute(stage_input)
            structured = _extract_json(result.content)
            ChangeResult.model_validate(structured)
            mcp_observed = any(
                event.kind == "mcp.tool_call" and (event.details or {}).get("tool_ref") == "muxdev-certification/ping"
                for event in result.events
            )
            acp_initialized = any(event.kind == "acp.initialized" for event in result.events)
            acp_initialize_event = next(
                (event for event in result.events if event.kind == "acp.initialized"), None
            )
            permission_observed = any(event.kind.startswith("permission.") for event in result.events)
            checks.update({
                "returncode": result.returncode,
                "structured_schema": True,
                "protocol": result.protocol,
                "stream_events": result.event_count,
                "stderr_summary": (result.stderr_content or "")[-2_000:],
                "mcp_fixture_observed": mcp_observed if grant.mcp_tools else None,
                "acp_initialized": acp_initialized if result.protocol == "acp" else None,
                "acp_agent_info": (
                    dict(acp_initialize_event.details or {}).get("agent_info")
                    if acp_initialize_event else None
                ),
                "permission_observed": permission_observed if result.protocol == "acp" else None,
                "residual_processes": list(ProcessSupervisor.active_pids(stage_input.run_id)),
            })
            valid = result.returncode == 0 and not checks["residual_processes"]
            if grant.mcp_tools:
                valid = valid and mcp_observed
            if result.protocol == "acp":
                valid = valid and bool(result.session_id) and acp_initialized and permission_observed
                cancel_check = _probe_acp_cancel(adapter, worktree, provider)
                checks["cancel"] = cancel_check
                valid = valid and bool(cancel_check["requested"]) and bool(cancel_check["cleaned"])
    except Exception as exc:
        checks.update({"structured_schema": False, "error": str(exc)})
        valid = False
    return {
        "provider": provider,
        "status": "live_verified" if valid else "failed",
        "live": True,
        "fingerprint": fingerprint,
        "capabilities": capabilities,
        "checks": checks,
    }


def certification_matches(workspace: Path, provider: str, row: dict[str, Any] | None) -> bool:
    if provider in {"mock", "replay"}:
        return True
    payload = row.get("payload") if row and isinstance(row.get("payload"), dict) else {}
    certified = payload.get("fingerprint") if isinstance(payload.get("fingerprint"), dict) else {}
    return certified.get("fingerprint") == provider_fingerprint(workspace, provider).get("fingerprint")


def _probe_acp_cancel(adapter: Any, worktree: Path, provider: str) -> dict[str, object]:
    from .acp import cancel_acp_run

    run_id = f"certify-{provider}-cancel"
    stage_input = StageExecutionInput(
        run_id=run_id,
        stage_id="cancel",
        role="code",
        task="Keep this ACP session active until the client cancels it. Do not modify files.",
        worktree=worktree,
        context={},
        capabilities=CapabilityGrant(read_workspace=True),
        provider=provider,
        policy={"output_schema": "ChangeResult", "timeout_seconds": 15},
    )
    outcome: dict[str, object] = {}

    def execute() -> None:
        try:
            outcome["result"] = adapter.execute(stage_input)
        except Exception as exc:
            outcome["error"] = str(exc)

    thread = threading.Thread(target=execute, name=f"muxdev-certify-cancel-{provider}", daemon=True)
    thread.start()
    requested = False
    deadline = time.monotonic() + 5
    while thread.is_alive() and time.monotonic() < deadline:
        if cancel_acp_run(run_id, timeout_seconds=2):
            requested = True
            break
        time.sleep(0.02)
    thread.join(timeout=5)
    if thread.is_alive():
        ProcessSupervisor().cancel(run_id)
        thread.join(timeout=3)
    residual = ProcessSupervisor.active_pids(run_id)
    return {
        "requested": requested,
        "cleaned": not thread.is_alive() and not residual,
        "residual_processes": list(residual),
        "error": outcome.get("error"),
    }


def _definition(workspace: Path, provider: str) -> dict[str, Any]:
    providers = load_config(workspace).get("providers", {})
    definition = providers.get(provider) if isinstance(providers, dict) else None
    if not isinstance(definition, dict):
        raise ValueError(f"unknown provider: {provider}")
    return definition


def _version(executable: str | None) -> str | None:
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, check=False, **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (result.stdout or result.stderr).strip().splitlines()[0][:300] if (result.stdout or result.stderr).strip() else None


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _extract_json(content: str) -> dict[str, object]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("provider did not return a structured JSON object")
