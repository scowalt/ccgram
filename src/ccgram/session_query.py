from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .session_resolver import ClaudeSession


async def resolve_session_for_window(window_id: str) -> "ClaudeSession | None":
    from .session_resolver import session_resolver

    return await session_resolver.resolve_session_for_window(window_id)


def find_users_for_session(session_id: str) -> list[tuple[int, str, int]]:
    from .session_resolver import session_resolver

    return session_resolver.find_users_for_session(session_id)


async def get_recent_messages(
    window_id: str,
    *,
    start_byte: int = 0,
    end_byte: int | None = None,
) -> tuple[list[dict], int]:
    from .session_resolver import session_resolver

    return await session_resolver.get_recent_messages(
        window_id, start_byte=start_byte, end_byte=end_byte
    )
