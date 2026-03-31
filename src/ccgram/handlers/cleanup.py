"""Unified cleanup API for topic state.

Provides centralized cleanup functions that coordinate state cleanup across
all modules, preventing memory leaks when topics are deleted.

Functions:
  - clear_topic_state: Clean up all memory state for a specific topic
  - clear_dead_notification (delegated): Clear dead window notification tracking
"""

from typing import Any

from telegram import Bot

from ..utils import log_throttle_reset
from .interactive_ui import clear_interactive_msg
from .message_queue import enqueue_status_update
from .user_state import PENDING_THREAD_ID, PENDING_THREAD_TEXT, VOICE_PENDING


def _clear_window_state(window_id: str, user_id: int, thread_id: int) -> None:
    """Clear state keyed by window_id or qualified_id.

    Window-scoped and qualified-scoped cleanups are handled by
    TopicStateRegistry.  This function retains only non-registerable
    cleanup (log throttle, mailbox I/O).
    """
    from ..config import config
    from ..mailbox import Mailbox
    from ..window_resolver import is_foreign_window

    log_throttle_reset(f"topic-probe:{window_id}")
    log_throttle_reset(f"status-update:{user_id}:{thread_id}")

    qualified_id = (
        window_id
        if is_foreign_window(window_id)
        else f"{config.tmux_session_name}:{window_id}"
    )
    mb = Mailbox(config.mailbox_dir)
    mb.sweep(qualified_id)
    mb.clear_inbox(qualified_id)


async def clear_topic_state(
    user_id: int,
    thread_id: int,
    bot: Bot | None = None,
    user_data: dict[str, Any] | None = None,
    window_id: str | None = None,
) -> None:
    """Clear all memory state associated with a topic.

    This should be called when:
      - A topic is closed or deleted
      - A thread binding becomes stale (window deleted externally)

    Removes full dict entries from _topic_poll_state / _window_poll_state
    (not just field resets) to prevent orphaned state accumulation.
    Also cleans up status messages, tool tracking, interactive UI, emoji,
    command history, and user_data pending state.
    """
    # Clear status message from Telegram (if bot available)
    if bot is not None:
        await enqueue_status_update(
            bot, user_id, window_id or "", None, thread_id=thread_id
        )

    if window_id:
        _clear_window_state(window_id, user_id, thread_id)

    # Clear interactive UI state (also deletes message from chat)
    await clear_interactive_msg(user_id, bot, thread_id)

    # Resolve chat_id once for voice cleanup and registry dispatch
    from ..thread_router import thread_router

    chat_id = thread_router.resolve_chat_id(user_id, thread_id)

    # Clear pending thread state from user_data
    if user_data is not None and user_data.get(PENDING_THREAD_ID) == thread_id:
        user_data.pop(PENDING_THREAD_ID, None)
        user_data.pop(PENDING_THREAD_TEXT, None)

    # Clear pending voice transcriptions for this chat
    if user_data is not None:
        voice_store: dict[tuple[int, int], str] = user_data.get(VOICE_PENDING, {})
        stale = [k for k in voice_store if k[0] == chat_id]
        for k in stale:
            voice_store.pop(k, None)

    # Dispatch all registered cleanups via registry
    from .topic_state_registry import topic_state

    qualified_id: str | None = None
    if window_id:
        from ..config import config
        from ..window_resolver import is_foreign_window

        qualified_id = (
            window_id
            if is_foreign_window(window_id)
            else f"{config.tmux_session_name}:{window_id}"
        )

    topic_state.clear_all(
        user_id,
        thread_id,
        window_id=window_id,
        qualified_id=qualified_id,
        chat_id=chat_id,
    )
