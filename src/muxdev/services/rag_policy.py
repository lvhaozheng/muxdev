"""Task-aware RAG enablement policy.

RAG is a contextual capability, not a default prompt tax. This module decides
whether a workflow stage should retrieve codebase evidence and records the
reason in context packets and traces.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path


RAG_INTENT_MARKERS = {
    "based on existing",
    "codebase",
    "existing implementation",
    "find",
    "locate",
    "reference",
    "architecture",
    "module",
    "cross-file",
    "根据现有",
    "现有实现",
    "查找",
    "定位",
    "参考",
    "架构",
    "模块",
    "跨文件",
}

LOW_VALUE_STAGE_IDS = {"approve_plan", "human_design_approval"}
LOW_VALUE_ROLES = {"test", "tester"}


@dataclass(frozen=True)
class RagDecision:
    enabled: bool
    reason: str
    query: str
    top_k: int = 3
    context_sources: list[str] = field(default_factory=list)
    skipped_because: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def decide_rag(
    *,
    task: str,
    stage_id: str,
    role: str | None,
    context_sources: list[str] | tuple[str, ...] | None,
    rag_query: str | None,
    index_path: Path | None,
    memory_items: list[dict[str, object]] | None = None,
) -> RagDecision:
    """Return whether RAG should be used for a stage and why."""
    sources = [str(item).lower() for item in context_sources or []]
    explicit = "rag" in sources
    query = (rag_query or task).strip()
    if explicit:
        return RagDecision(True, "stage explicitly requested rag context", query, context_sources=list(sources))

    normalized_task = task.lower()
    if stage_id in LOW_VALUE_STAGE_IDS or (role or "").lower() in LOW_VALUE_ROLES:
        return RagDecision(False, "stage is low-value for retrieval context", query, context_sources=list(sources), skipped_because="stage_scope")

    memory_has_project_context = any(
        str(item.get("layer") or item.get("scope") or "").lower() in {"project", "workspace"}
        and str(item.get("status") or item.get("promotion_state") or "").lower() in {"active", "approved"}
        for item in memory_items or []
        if isinstance(item, dict)
    )
    if memory_has_project_context and not _has_rag_intent(normalized_task):
        return RagDecision(False, "approved memory context is sufficient for this stage", query, context_sources=list(sources), skipped_because="memory_sufficient")

    if not _has_rag_intent(normalized_task):
        return RagDecision(False, "task does not require codebase retrieval evidence", query, context_sources=list(sources), skipped_because="no_rag_intent")

    if index_path is not None and not index_path.exists():
        return RagDecision(False, "rag index is absent and the task can proceed without cold-start indexing", query, context_sources=list(sources), skipped_because="index_missing")

    return RagDecision(True, "task appears to require codebase or architecture context", query, context_sources=list(sources))


def _has_rag_intent(text: str) -> bool:
    return any(marker in text for marker in RAG_INTENT_MARKERS)

