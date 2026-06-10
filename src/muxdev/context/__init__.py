"""Context assembly layer for runtime prompts and snapshots."""

from .assembler import build_context_packet, memory_refs, task_with_context_packet, task_with_memory_context, write_context_packet

__all__ = [
    "build_context_packet",
    "memory_refs",
    "task_with_context_packet",
    "task_with_memory_context",
    "write_context_packet",
]
