from __future__ import annotations

import structlog
from typing import TYPE_CHECKING

from ..telegram_client import TelegramClient
from ..thread_router import thread_router
from ..multiplexer import multiplexer as tmux_manager
from ..window_query import get_window_provider

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

_TELEGRAM_MAX = 4096


async def send_last_reply(
    client: TelegramClient,
    chat_id: int,
    thread_id: int | None,
    window_id: str,
) -> None:
    from ..providers import get_provider_for_window

    from ..last_unit import extract_last_shell_block

    provider_name = get_window_provider(window_id)
    provider = get_provider_for_window(window_id, provider_name=provider_name)
    caps = provider.capabilities

    if caps.name == "shell":
        scrollback = await tmux_manager.capture_pane_scrollback(window_id, history=200)
        block = extract_last_shell_block(scrollback) if scrollback else None
        text = block if block is not None else "No command output found."
    elif caps.supports_structured_transcript:
        text = await _extract_last_ai_reply(window_id)
    else:
        scrollback = await tmux_manager.capture_pane_scrollback(window_id, history=200)
        text = scrollback.strip() if scrollback else "No reply yet."

    await _deliver(client, chat_id, thread_id, window_id, text)


async def _extract_last_ai_reply(window_id: str) -> str:
    from .. import session_query

    messages, _ = await session_query.get_recent_messages(window_id)

    result = _collect_last_turn_blocks(messages)
    if result:
        return result
    return _most_recent_assistant_text(messages)


def _collect_last_turn_blocks(messages: list[dict]) -> str:
    last_user_idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    if last_user_idx is None:
        return ""

    blocks: list[str] = []
    for msg in messages[last_user_idx + 1 :]:
        role = msg.get("role")
        if role == "assistant" and msg.get("content_type") == "text":
            t = (msg.get("text") or "").strip()
            if t:
                blocks.append(t)
        elif role == "user":
            break
    return "\n\n".join(blocks)


def _most_recent_assistant_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content_type") == "text":
            t = (msg.get("text") or "").strip()
            if t:
                return t
    return "No reply yet."


async def _deliver(
    client: TelegramClient,
    chat_id: int,
    thread_id: int | None,
    window_id: str,
    text: str,
) -> None:
    from .messaging_pipeline.message_sender import safe_send

    if len(text) <= _TELEGRAM_MAX:
        await safe_send(client, chat_id, text, message_thread_id=thread_id)
        return

    import tempfile

    from pathlib import Path

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".txt",
            mode="w",
            encoding="utf-8",
        ) as fh:
            fh.write(text)
            tmp_path = fh.name

        window_label = (
            "".join(c if c.isalnum() or c in "_-" else "-" for c in window_id).strip(
                "-"
            )
            or "reply"
        )
        filename = f"last-reply-{window_label}.txt"
        await client.send_document(
            chat_id=chat_id,
            document=Path(tmp_path),
            filename=filename,
            **({"message_thread_id": thread_id} if thread_id is not None else {}),
        )
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)


async def last_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    from ..config import config

    from ..utils import handle_general_topic_message, is_general_topic

    from .messaging_pipeline.message_sender import safe_reply

    from ..telegram_client import PTBTelegramClient

    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    from .callback_helpers import get_thread_id

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

    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        await safe_reply(update.message, "❌ Window no longer exists.")
        return

    chat_id = thread_router.resolve_chat_id(user.id, thread_id)
    client = PTBTelegramClient(update.message.get_bot())
    await send_last_reply(client, chat_id, thread_id, window_id)
