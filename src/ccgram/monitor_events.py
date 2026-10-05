from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SessionInfo:
    session_id: str
    file_path: Path


@dataclass
class NewMessage:
    session_id: str
    text: str
    is_complete: bool
    content_type: str = "text"
    phase: str | None = None
    tool_use_id: str | None = None
    role: str = "assistant"
    tool_name: str | None = None


@dataclass
class NewWindowEvent:
    window_id: str
    session_id: str
    window_name: str
    cwd: str
