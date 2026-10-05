from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..claude_task_state import ClaudeTaskSnapshot


@dataclass(frozen=True, slots=True)
class LiveSessionSnapshot:
    window_id: str
    session_id: str | None
    task_snapshot: "ClaudeTaskSnapshot | None"
    wait_header: str | None
    last_activity_ts: float | None


def get_task_snapshot(window_id: str) -> "ClaudeTaskSnapshot | None":
    from ..claude_task_state import get_claude_task_snapshot

    return get_claude_task_snapshot(window_id)


def has_task_snapshot(window_id: str) -> bool:
    from ..claude_task_state import claude_task_state

    return claude_task_state.has_snapshot(window_id)


def get_wait_header(window_id: str) -> str | None:
    from ..claude_task_state import get_claude_wait_header

    return get_claude_wait_header(window_id)


def get_session_id(window_id: str) -> str | None:
    from ..session_lifecycle import session_lifecycle

    return session_lifecycle.resolve_session_id(window_id)


def get_last_activity_ts(window_id: str) -> float | None:
    from ..session_lifecycle import session_lifecycle

    from ..session_monitor import get_active_monitor

    session_id = session_lifecycle.resolve_session_id(window_id)
    if not session_id:
        return None
    monitor = get_active_monitor()
    if monitor is None:
        return None
    return monitor.get_last_activity(session_id)


def get_live_session_snapshot(window_id: str) -> LiveSessionSnapshot:
    return LiveSessionSnapshot(
        window_id=window_id,
        session_id=get_session_id(window_id),
        task_snapshot=get_task_snapshot(window_id),
        wait_header=get_wait_header(window_id),
        last_activity_ts=get_last_activity_ts(window_id),
    )


__all__ = [
    "LiveSessionSnapshot",
    "get_last_activity_ts",
    "get_live_session_snapshot",
    "get_session_id",
    "get_task_snapshot",
    "get_wait_header",
    "has_task_snapshot",
]
