"""Small in-process event bus for daemon websocket subscribers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


MAX_SUBSCRIBER_EVENTS = 256
MAX_EVENT_BYTES = 64 * 1024


@dataclass
class EventBus:
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_SUBSCRIBER_EVENTS)
        queue.put_nowait({"type": "hello", "message": "muxdev events connected"})
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        try:
            size = len(json.dumps(event, ensure_ascii=False, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            size = MAX_EVENT_BYTES + 1
        if size > MAX_EVENT_BYTES:
            event = {
                "type": "event_truncated", "version": 1,
                "original_type": str(event.get("type") or "unknown")[:100],
                "run_id": str(event.get("run_id") or event.get("task_id") or "")[:200],
            }
        for queue in list(self.subscribers):
            try:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(event)
            except Exception:
                self.subscribers.discard(queue)
