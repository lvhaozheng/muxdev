"""Runtime isolation failures that require explicit reconciliation."""


class ReconciliationRequired(RuntimeError):
    """A durable worktree exists but cannot be safely reused automatically."""
