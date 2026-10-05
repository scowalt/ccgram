from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..telegram_client import PTBTelegramClient, TelegramClient

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

from ..config import config
from ..session_map import session_map_prefix
from ..thread_router import thread_router
from ..topic_state_registry import topic_state
from ..utils import handle_general_topic_message, is_general_topic, log_throttle_reset
from .callback_helpers import get_thread_id
from .interactive import clear_interactive_msg
from .messaging_pipeline.message_queue import enqueue_status_update
from .messaging_pipeline.message_sender import safe_reply
from .status.status_bubble import clear_status_msg_info
from .user_state import PENDING_THREAD_ID, PENDING_THREAD_TEXT, VOICE_PENDING


async def clear_topic_state(
    user_id: int,
    thread_id: int,
    client: TelegramClient | None = None,
    user_data: dict[str, Any] | None = None,
    window_id: str | None = None,
    *,
    window_dead: bool = True,
) -> None:
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)

    qualified_id: str | None = None
    if window_id and window_dead:
        qualified_id = f"{session_map_prefix()}{window_id}"

    if client is not None:
        await enqueue_status_update(
            client,
            user_id,
            window_id or "",
            None,
            thread_id=thread_id,
        )
    else:
        clear_status_msg_info(user_id, thread_id)

    topic_state.clear_all(
        user_id,
        thread_id,
        window_id=window_id,
        qualified_id=qualified_id,
        chat_id=chat_id,
    )
    if window_id and window_dead:
        from .shell.shell_prompt_orchestrator import clear_state as _clear_shell_prompt

        _clear_shell_prompt(window_id)

    log_throttle_reset(f"status-update:{user_id}:{thread_id}")
    if window_id:
        log_throttle_reset(f"topic-probe:{window_id}")

    await clear_interactive_msg(user_id, client, thread_id)

    if user_data is not None and user_data.get(PENDING_THREAD_ID) == thread_id:
        user_data.pop(PENDING_THREAD_ID, None)
        user_data.pop(PENDING_THREAD_TEXT, None)

    if user_data is not None:
        voice_store: dict[tuple[int, int], str] = user_data.get(VOICE_PENDING, {})
        stale = [k for k in voice_store if k[0] == chat_id]
        for k in stale:
            voice_store.pop(k, None)


async def unbind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = get_thread_id(update)
    if thread_id is None:
        if (
            update.message
            and update.effective_chat
            and is_general_topic(update.message)
        ):
            await handle_general_topic_message(
                update.get_bot(), update.message, update.effective_chat.id
            )
        else:
            await safe_reply(update.message, "❌ Use this command inside a topic.")
        return

    window_id = thread_router.get_window_for_thread(user.id, thread_id)
    if not window_id:
        await safe_reply(update.message, "❌ This topic is not bound to any session.")
        return

    display = thread_router.get_display_name(window_id)
    client = PTBTelegramClient(context.bot)
    await enqueue_status_update(client, user.id, window_id, None, thread_id)
    await clear_topic_state(
        user.id,
        thread_id,
        client,
        context.user_data,
        window_id=window_id,
        window_dead=False,
    )
    thread_router.unbind_thread(user.id, thread_id)
    await safe_reply(
        update.message,
        f"✂ Unbound from window `{display}`. The session is still running.\n"
        "Send a message in this topic to rebind or create a new session.",
    )
