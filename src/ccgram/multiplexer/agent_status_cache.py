from __future__ import annotations

from .base import AgentStatus

_cache: dict[str, AgentStatus] = {}


def set_status(window_id: str, status: AgentStatus) -> None:
    _cache[window_id] = status


def get_status(window_id: str) -> AgentStatus | None:
    return _cache.get(window_id)


def clear(window_id: str) -> None:
    _cache.pop(window_id, None)


def reset() -> None:
    _cache.clear()
