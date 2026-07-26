"""Structured lifecycle errors shared by Agent Session runtime components."""

from __future__ import annotations

from pathlib import Path


class SessionLifecycleError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        remediation: str,
        retryable: bool,
        session_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.remediation = remediation
        self.retryable = retryable
        self.session_id = session_id

    def detail(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            "remediation": self.remediation,
            "retryable": self.retryable,
            "session_id": self.session_id,
        }


class SessionCapacityError(SessionLifecycleError):
    def __init__(self, workspace: Path) -> None:
        super().__init__(
            "Workbench CLI 并发容量已满，Assignment 已进入公平等待队列。",
            code="session_capacity_queued",
            remediation="等待其他 Agent 命令结束；Daemon 会自动启动排队任务。",
            retryable=True,
        )
        self.workspace = workspace


__all__ = ["SessionCapacityError", "SessionLifecycleError"]
