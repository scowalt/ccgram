import time
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

ContentType: TypeAlias = Literal["text", "tool_use", "tool_result"]
MessageRole: TypeAlias = Literal["assistant", "user"]


@dataclass(frozen=True, slots=True)
class ContentTask:
    window_id: str
    parts: tuple[str, ...]
    content_type: ContentType = "text"
    role: MessageRole = "assistant"
    tool_use_id: str | None = None
    tool_name: str | None = None
    thread_id: int | None = None
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, slots=True)
class StatusUpdateTask:
    window_id: str
    text: str | None
    thread_id: int | None = None
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, slots=True)
class StatusClearTask:
    window_id: str | None
    thread_id: int | None = None
    enqueued_at: float = field(default_factory=time.monotonic)


MessageTask: TypeAlias = ContentTask | StatusUpdateTask | StatusClearTask


def thread_key(thread_id: int | None) -> int:
    return thread_id or 0
