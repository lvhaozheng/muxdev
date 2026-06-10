"""Daemon worker queue primitives."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class TaskQueue:
    lock: threading.RLock = field(default_factory=threading.RLock)
    workers: dict[str, threading.Thread] = field(default_factory=dict)

    def start(self, task_id: str, thread: threading.Thread) -> None:
        with self.lock:
            self.workers[task_id] = thread
        thread.start()

    def start_if_idle(self, task_id: str, thread: threading.Thread) -> bool:
        with self.lock:
            existing = self.workers.get(task_id)
            if existing and existing.is_alive():
                return False
            self.workers[task_id] = thread
        thread.start()
        return True
