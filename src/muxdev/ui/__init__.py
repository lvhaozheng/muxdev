"""Interactive REPL package.

The daemon-backed TUI lives in :mod:`muxdev.cli.tui`; the removed workspace
TUI no longer has a second state-management path.
"""

from .repl import handle_repl_command, start_repl

__all__ = [
    "handle_repl_command",
    "start_repl",
]
