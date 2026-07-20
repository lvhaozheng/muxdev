"""Small deterministic repository map; no embeddings, index, or memory."""

from __future__ import annotations

import ast
import re
from pathlib import Path


IGNORED = {".git", ".muxdev", ".pytest_cache", ".venv", "node_modules", "__pycache__"}


def build_repo_map(workspace: Path, query: str, *, max_files: int = 40, max_chars: int = 8_000) -> str:
    """Return relevant Python paths and signatures in stable rank order."""
    tokens = {item.lower() for item in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)}
    candidates: list[tuple[int, str, list[str]]] = []
    for path in sorted(workspace.rglob("*.py")):
        relative = path.relative_to(workspace).as_posix()
        if any(part in IGNORED for part in path.relative_to(workspace).parts) or path.stat().st_size > 256_000:
            continue
        symbols = _python_symbols(path)
        searchable = f"{relative} {' '.join(symbols)}".lower()
        score = sum(3 if token in relative.lower() else 1 for token in tokens if token in searchable)
        candidates.append((score, relative, symbols))
    ranked = sorted(candidates, key=lambda item: (-item[0], item[1]))[:max_files]
    lines: list[str] = []
    for _, relative, symbols in ranked:
        entry = relative + (": " + "; ".join(symbols[:20]) if symbols else "")
        if sum(len(line) + 1 for line in lines) + len(entry) > max_chars:
            break
        lines.append(entry)
    return "\n".join(lines)


def _python_symbols(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    rows: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "def"
            args = ""
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = "(" + ", ".join(arg.arg for arg in node.args.args[:8]) + ")"
            rows.append((node.lineno, f"{kind} {node.name}{args}"))
    return [value for _, value in sorted(rows)]
