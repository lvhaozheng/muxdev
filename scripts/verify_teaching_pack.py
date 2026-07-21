"""Verify the Chinese muxdev teaching and interview-defense pack."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "release-artifacts"
PLAYBOOK = ARTIFACTS / "muxdev-teaching-playbook-cn.md"
BANK = ARTIFACTS / "muxdev-interview-drill-bank-cn.md"
QA = ARTIFACTS / "interview-qa-cn.md"
LOGIN = ARTIFACTS / "login-evidence-example.json"

DOMAINS = tuple("PAVDEWMRTQOBL")
QUESTION_ID = re.compile(r"^\| ([A-Z]\d{2}) \|", re.MULTILINE)

CODE_PROOFS = {
    "src/muxdev/runtime/engine.py": (
        "class RunEngine",
        "def _execute_readonly_fanout",
        "def _repair_change",
    ),
    "src/muxdev/runtime/stage_attempt.py": ("def _run_check", "def _repair_invalid_output"),
    "src/muxdev/workflows/engine.py": ("def validate_dag", "def execution_waves"),
    "src/muxdev/providers/protocols.py": ("def build_cli_invocation", "def parse_cli_output"),
    "src/muxdev/services/context.py": ("def build_context_pack", "def retrieve_verified_memory"),
    "src/muxdev/services/gate.py": ("def evaluate_gate", "def _evaluate_requirement"),
    "src/muxdev/services/evidence_verify.py": ("def verify_evidence_report",),
    "src/muxdev/runtime/worktree.py": ("class WorktreeManager", "def prepare"),
    "src/muxdev/storage/control.py": ("class ControlStore", "def verify_event_chain"),
}

HONESTY_BOUNDARIES = (
    "Provider Action 双向暂停/恢复目前未接通",
    "证据链不等于任意节点快照回滚",
    "并行写 Worker 尚不支持",
    "当前不依赖 LangGraph",
)


def verify() -> list[str]:
    errors: list[str] = []
    for path in (PLAYBOOK, BANK, QA, LOGIN, ARTIFACTS / "open-source-research.md"):
        if not path.is_file():
            errors.append(f"missing teaching artifact: {path.relative_to(ROOT)}")
    if errors:
        return errors

    playbook = PLAYBOOK.read_text(encoding="utf-8")
    bank = BANK.read_text(encoding="utf-8")
    qa = QA.read_text(encoding="utf-8")

    ids = QUESTION_ID.findall(bank)
    if len(ids) != 130:
        errors.append(f"expected 130 question families, found {len(ids)}")
    if len(ids) != len(set(ids)):
        errors.append("question family IDs are not unique")
    expected = {f"{domain}{number:02d}" for domain in DOMAINS for number in range(1, 11)}
    missing = sorted(expected - set(ids))
    unexpected = sorted(set(ids) - expected)
    if missing:
        errors.append(f"missing question IDs: {missing}")
    if unexpected:
        errors.append(f"unexpected question IDs: {unexpected}")

    q_numbers = {int(value) for value in re.findall(r"^## Q(\d+)：", qa, re.MULTILINE)}
    if q_numbers != set(range(1, 23)):
        errors.append(f"Q1-Q22 source is incomplete: {sorted(q_numbers)}")

    for boundary in HONESTY_BOUNDARIES:
        if boundary not in playbook:
            errors.append(f"missing honesty boundary: {boundary}")

    if len(re.findall(r"^### 规则", playbook, re.MULTILINE)) != 10:
        errors.append("teaching playbook must contain exactly ten teaching rules")
    units = re.findall(r"^\| \d+ \|", playbook, re.MULTILINE)
    if len(units) != 10:
        errors.append(f"expected ten curriculum units, found {len(units)}")
    difficulty_section = re.search(r"(?s)## 6\..*?(?=## 7\.)", playbook)
    difficulty_rows = (
        [
            line
            for line in difficulty_section.group(0).splitlines()
            if line.startswith("| ") and not line.startswith(("|---", "| 难点 |"))
        ]
        if difficulty_section
        else []
    )
    if len(difficulty_rows) < 20:
        errors.append(f"expected at least 20 grounded engineering difficulties, found {len(difficulty_rows)}")
    if len(re.findall(r"^- \[ \]", playbook, re.MULTILINE)) < 12:
        errors.append("teaching completion audit must contain at least 12 explicit checks")
    if playbook.count("|---") < 10:
        errors.append("teaching playbook is missing its structured teaching matrices")
    if "C-P-M-C-B" not in playbook or "主张—证据—代码" not in playbook:
        errors.append("teaching answer and evidence rules are incomplete")

    login = json.loads(LOGIN.read_text(encoding="utf-8"))
    if not login.get("subject", {}).get("simulated") or login.get("decision", {}).get("status") != "PASS":
        errors.append("login evidence example must be simulated and reproducibly PASS")

    for relative, symbols in CODE_PROOFS.items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"missing code proof: {relative}")
            continue
        source = path.read_text(encoding="utf-8")
        for symbol in symbols:
            if symbol not in source:
                errors.append(f"missing code symbol: {relative}::{symbol}")
    return errors


def main() -> int:
    errors = verify()
    if errors:
        raise SystemExit("teaching pack verification failed:\n- " + "\n- ".join(errors))
    print(
        "teaching pack verified: 130 question families across 13 domains, "
        "Q1-Q22, login evidence, honesty boundaries, and code proofs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
