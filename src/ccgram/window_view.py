from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WindowView:
    window_id: str
    cwd: str
    provider_name: str
    approval_mode: str
    batch_mode: str
    tool_call_visibility: str
    transcript_path: Path | None
    window_name: str
    session_id: str
    origin: str
