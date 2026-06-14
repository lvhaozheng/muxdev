"""Optional LLM-as-a-Judge support for validation experiments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.redaction import redact
from ..models.validation import StrategyRun
from ..providers.adapters import extract_json_object, get_runtime_provider
from ..storage import Blackboard, RunStore
from ..storage.contracts import write_json_artifact


def judge_validation_run(workspace: Path, strategy_run: StrategyRun, *, judge_provider: str) -> dict[str, Any]:
    """Evaluate one validation run with a configured provider adapter."""
    from ..runtime.stage_attempt import run_provider_stage

    run_dir = RunStore(workspace).find_run_dir(strategy_run.run_id)
    payload = _judge_input(run_dir, strategy_run)
    prompt = _judge_prompt(payload)
    judge_worktree = run_dir / "validation" / "judge_worktree"
    judge_worktree.mkdir(parents=True, exist_ok=True)
    output = run_provider_stage(
        get_runtime_provider(judge_provider),
        stage_id="judge",
        task=prompt,
        worktree=judge_worktree,
        skills=[],
        session_dir=run_dir / "provider_sessions",
    )
    parsed = extract_json_object(output.content) or {}
    result = _normalize_judge_result(parsed, raw_output=output.content, judge_provider=judge_provider)
    result["input"] = payload
    path, digest = write_json_artifact(run_dir / "validation" / f"judge_{strategy_run.strategy}.json", result)
    result["path"] = str(path)
    result["artifact_hash"] = digest
    return result


def _judge_input(run_dir: Path, strategy_run: StrategyRun) -> dict[str, Any]:
    board = Blackboard(run_dir)
    try:
        run = board.get_run(strategy_run.run_id)
        stages = board.table_rows("stages", run_id=strategy_run.run_id)
        tests = board.table_rows("test_results", run_id=strategy_run.run_id)
        blockers = board.table_rows("review_blockers", run_id=strategy_run.run_id)
        errors = board.table_rows("error_details", run_id=strategy_run.run_id)
    finally:
        board.close()
    evidence = _read_json(run_dir / "evidence" / "evaluation.json")
    return {
        "task_id": strategy_run.task_id,
        "strategy": strategy_run.strategy,
        "mode": strategy_run.mode,
        "run_id": strategy_run.run_id,
        "provider": strategy_run.provider,
        "workflow": strategy_run.workflow,
        "task": run.get("task"),
        "output": _read_text(strategy_run.output_path),
        "diff": _read_text(strategy_run.diff_path),
        "evidence_evaluation": evidence,
        "stages": [
            {
                "stage_id": row.get("stage_id"),
                "role": row.get("role"),
                "status": row.get("status"),
                "summary": row.get("summary"),
            }
            for row in stages
        ],
        "tests": tests,
        "review_blockers": blockers,
        "errors": errors,
    }


def _judge_prompt(payload: dict[str, Any]) -> str:
    compact = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "You are the muxdev validation judge. Evaluate whether the candidate run solved the user task. "
        "Use the trace/evidence only as support; judge the actual usefulness and safety of the final result. "
        "Return exactly one JSON object with these fields: score, pass, task_completion, answer_quality, "
        "groundedness, safety, process_quality, reasons, risks. Scores must be 0.0 to 1.0.\n\n"
        f"Validation input:\n{compact}"
    )


def _normalize_judge_result(parsed: dict[str, Any], *, raw_output: str, judge_provider: str) -> dict[str, Any]:
    score = _score(parsed.get("score"))
    return {
        "contract_version": "muxdev.validation_judge.v1",
        "judge_provider": judge_provider,
        "score": score if score is not None else 0.0,
        "pass": bool(parsed.get("pass")) if parsed.get("pass") is not None else bool(score is not None and score >= 0.7),
        "task_completion": _score(parsed.get("task_completion")),
        "answer_quality": _score(parsed.get("answer_quality")),
        "groundedness": _score(parsed.get("groundedness")),
        "safety": _score(parsed.get("safety")),
        "process_quality": _score(parsed.get("process_quality")),
        "reasons": _string_list(parsed.get("reasons")) or ["judge returned no structured reasons"],
        "risks": _string_list(parsed.get("risks")),
        "raw_output": redact(raw_output),
    }


def _score(value: object) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score > 1.0:
        score = score / 100.0
    return max(0.0, min(1.0, score))


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _read_text(path: str | None, *, max_chars: int = 12000) -> str:
    if not path:
        return ""
    candidate = Path(path)
    if not candidate.exists() or not candidate.is_file():
        return ""
    text = candidate.read_text(encoding="utf-8", errors="replace")
    return redact(text[:max_chars])


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
