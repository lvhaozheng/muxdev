"""Runtime composition public API with lazy imports to avoid adapter cycles."""

from __future__ import annotations

from typing import Any


__all__ = [
    "CONVERSATION_ACTIONS",
    "ConversationService",
    "RunEngine",
    "RunResult",
    "WorktreeManager",
    "WorktreeResult",
    "new_run_id",
]


def __getattr__(name: str) -> Any:
    if name in {"CONVERSATION_ACTIONS", "ConversationService"}:
        from .conversation_service import CONVERSATION_ACTIONS, ConversationService

        return {"CONVERSATION_ACTIONS": CONVERSATION_ACTIONS, "ConversationService": ConversationService}[name]
    if name in {"RunEngine", "RunResult", "new_run_id"}:
        from .engine import RunEngine, RunResult, new_run_id

        return {"RunEngine": RunEngine, "RunResult": RunResult, "new_run_id": new_run_id}[name]
    if name in {"WorktreeManager", "WorktreeResult"}:
        from .worktree import WorktreeManager, WorktreeResult

        return {"WorktreeManager": WorktreeManager, "WorktreeResult": WorktreeResult}[name]
    raise AttributeError(name)
