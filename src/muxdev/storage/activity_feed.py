"""In-process wakeups for durable Conversation activity streams."""

from __future__ import annotations

import threading
from pathlib import Path


class ActivityFeed:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._versions: dict[tuple[str, str], int] = {}

    @staticmethod
    def _key(workspace: Path | str, conversation_id: str) -> tuple[str, str]:
        return (str(Path(workspace).resolve()), conversation_id)

    def publish(
        self, workspace: Path | str, conversation_id: str, sequence: int
    ) -> None:
        key = self._key(workspace, conversation_id)
        with self._condition:
            self._versions[key] = max(sequence, self._versions.get(key, 0))
            self._condition.notify_all()

    def wait(
        self,
        workspace: Path | str,
        conversation_id: str,
        after: int,
        timeout: float = 1.0,
    ) -> int:
        key = self._key(workspace, conversation_id)
        with self._condition:
            if self._versions.get(key, 0) <= after:
                self._condition.wait(timeout=max(0.05, timeout))
            return self._versions.get(key, after)


activity_feed = ActivityFeed()


__all__ = ["ActivityFeed", "activity_feed"]
