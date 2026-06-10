"""Provider action contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ProviderActionRequest:
    run_id: str
    stage_id: str
    provider: str
    kind: str
    prompt_text: str
    role: str | None = None
    options: tuple[Mapping[str, object], ...] = ()
    transcript_path: str | None = None
    chunks_path: str | None = None
