"""Local retrieval index used by CLI and MCP search tools.

The default embedding path is deterministic and offline, making tests stable and
avoiding network/model calls. Projects can opt into external embeddings through
environment variables when they want semantic search quality over reproducible
local behavior.
"""

from __future__ import annotations

import json
import math
import re
import hashlib
import os
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config.loader import load_config, path_config
from ..core.platforms import hidden_subprocess_kwargs


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class IndexedChunk:
    """One indexed file segment with lexical terms and an embedding vector."""

    path: str
    start_line: int
    end_line: int
    text: str
    terms: dict[str, int]
    embedding: list[float]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class LocalRagIndex:
    """Build and query a small JSON retrieval index inside the runtime path."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.path = path_config(workspace, "rag") / "index.json"

    def build(self, *, chunk_lines: int = 40) -> dict[str, object]:
        """Chunk indexable files and persist the resulting local index."""
        chunks: list[IndexedChunk] = []
        for path in _iter_indexable_files(self.workspace):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for offset in range(0, len(lines), chunk_lines):
                block = lines[offset : offset + chunk_lines]
                text = "\n".join(block).strip()
                if not text:
                    continue
                chunks.append(
                    IndexedChunk(
                        path=path.relative_to(self.workspace).as_posix(),
                        start_line=offset + 1,
                        end_line=offset + len(block),
                        text=text,
                        terms=dict(Counter(_tokens(text))),
                        embedding=embed_text(text),
                    )
                )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "chunks": [chunk.to_dict() for chunk in chunks]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(self.path), "chunks": len(chunks)}

    def query(self, query: str, *, limit: int = 5) -> list[dict[str, object]]:
        """Return the highest-scoring chunks for a query."""
        if not self.path.exists():
            self.build()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        chunks = [IndexedChunk(**item) for item in payload.get("chunks", [])]
        query_terms = Counter(_tokens(query))
        query_embedding = embed_text(query)
        scored = [
            (
                score_chunk(chunk, query_terms, len(chunks), _document_frequency(chunks))
                + cosine_similarity(query_embedding, chunk.embedding),
                chunk,
            )
            for chunk in chunks
        ]
        rows: list[dict[str, object]] = []
        for score, chunk in sorted(scored, key=lambda item: item[0], reverse=True):
            if score <= 0:
                continue
            rows.append(
                {
                    "path": chunk.path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "score": round(score, 6),
                    "citation": f"{chunk.path}:{chunk.start_line}-{chunk.end_line}",
                    "backend": "local-json",
                    "explain": "tfidf lexical score + deterministic hash embedding cosine similarity",
                    "text": chunk.text[:800],
                }
            )
            if len(rows) >= limit:
                break
        return rows


def score_chunk(chunk: IndexedChunk, query_terms: Counter[str], total_docs: int, doc_freq: dict[str, int]) -> float:
    """Compute a simple TF-IDF-style lexical score for one chunk."""
    score = 0.0
    for term, q_count in query_terms.items():
        tf = chunk.terms.get(term, 0)
        if tf == 0:
            continue
        idf = math.log((1 + total_docs) / (1 + doc_freq.get(term, 0))) + 1
        score += q_count * tf * idf
    return score


def embed_text(text: str, *, dimensions: int = 64) -> list[float]:
    """Embed text using external config or deterministic feature hashing."""
    external = external_embedding(text)
    if external:
        return external
    vector = [0.0] * dimensions
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    return sum(left[index] * right[index] for index in range(size))


def external_embedding(text: str) -> list[float] | None:
    """Call an optional external embedding source and normalize its vector."""
    file_path = os.environ.get("MUXDEV_EMBEDDING_FILE")
    if file_path:
        try:
            payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
            vector = payload.get("embedding", payload) if isinstance(payload, dict) else payload
            return _normalize_vector(vector)
        except Exception:
            return None
    command = os.environ.get("MUXDEV_EMBEDDING_COMMAND")
    if not command:
        return None
    try:
        completed = subprocess.run(
            command,
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=True,
            timeout=30,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    vector = payload.get("embedding", payload) if isinstance(payload, dict) else payload
    return _normalize_vector(vector)


def _normalize_vector(vector: object) -> list[float] | None:
    if not isinstance(vector, list):
        return None
    values: list[float] = []
    for item in vector:
        try:
            values.append(float(item))
        except (TypeError, ValueError):
            return None
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values] if norm else values


def _document_frequency(chunks: list[IndexedChunk]) -> dict[str, int]:
    df: Counter[str] = Counter()
    for chunk in chunks:
        df.update(chunk.terms.keys())
    return dict(df)


def _iter_indexable_files(workspace: Path):
    runtime_root = str(load_config(workspace).get("paths", {}).get("runtime_root", ".muxdev"))
    ignored_dirs = {".git", runtime_root, ".muxdev", ".pytest_cache", "__pycache__"}
    suffixes = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json"}
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace)
        if any(part in ignored_dirs for part in rel.parts):
            continue
        if path.suffix.lower() in suffixes:
            yield path


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]
