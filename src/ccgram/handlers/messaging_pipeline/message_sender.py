import asyncio
import contextlib
import structlog
import time
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import CallbackQuery, LinkPreviewOptions, Message, ReactionTypeEmoji
from telegram.error import BadRequest, RetryAfter, TelegramError

from ...config import config
from ...entity_formatting import convert_to_entities
from ...telegram_client import TelegramClient
from ...telegram_sender import TELEGRAM_MAX_MESSAGE_LENGTH
from ..reactions import (
    ALLOWED_REACTIONS,
    REACT_DONE,
    REACT_FAIL,
    REACT_INBOX,
    REACT_RUNNING,
    REACT_SEEN,
    REACT_THINKING,
    clear_reaction,
    react,
)

__all__ = [
    "ALLOWED_REACTIONS",
    "REACT_DONE",
    "REACT_FAIL",
    "REACT_INBOX",
    "REACT_RUNNING",
    "REACT_SEEN",
    "REACT_THINKING",
    "ack_reaction",
    "clear_reaction",
    "edit_with_fallback",
    "is_thread_gone",
    "rate_limit_send",
    "rate_limit_send_message",
    "react",
    "safe_edit",
    "safe_reply",
    "safe_send",
    "send_kwargs",
]

logger = structlog.get_logger()

_SEND_WARN_SECS = 1.0


def is_thread_gone(exc: TelegramError) -> bool:
    if isinstance(exc, BadRequest):
        msg = exc.message.lower()
        return "thread not found" in msg or "topic_id_invalid" in msg
    return False


NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)


class _MessageGoneError(Exception):
    pass


def _retry_after_seconds(exc: RetryAfter) -> int:
    ra = exc.retry_after
    return ra if isinstance(ra, int) else int(ra.total_seconds())


_last_send_time: dict[int, float] = {}
_rate_limit_locks: dict[int, asyncio.Lock] = {}
MESSAGE_SEND_INTERVAL = 0.5


async def rate_limit_send(chat_id: int) -> None:
    lock = _rate_limit_locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        if chat_id in _last_send_time:
            target = _last_send_time[chat_id] + MESSAGE_SEND_INTERVAL
            if target > now:
                await asyncio.sleep(target - now)
                _last_send_time[chat_id] = time.monotonic()
                return
        _last_send_time[chat_id] = time.monotonic()


def _cap_to_telegram_limit(
    plain_text: str, entities: list[Any]
) -> tuple[str, list[Any]]:
    if len(plain_text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return plain_text, entities
    ellipsis = "…"
    cap = TELEGRAM_MAX_MESSAGE_LENGTH - len(ellipsis)
    truncated = plain_text[:cap] + ellipsis
    kept = [e for e in entities if e.offset + e.length <= cap]
    return truncated, kept


async def _with_entity_fallback(
    send_fn: Callable[..., Awaitable[Any]],
    text: str,
    context_label: str,
    **kwargs: Any,
) -> Message | None:
    plain_text, entities = convert_to_entities(text)

    if not plain_text.strip():
        return None

    plain_text, entities = _cap_to_telegram_limit(plain_text, entities)

    last_error: TelegramError | None = None
    for phase_entities in (entities, None):
        send_kwargs = {**kwargs}
        if phase_entities is not None:
            send_kwargs["entities"] = phase_entities
        try:
            return await send_fn(plain_text, **send_kwargs)
        except RetryAfter as e:
            await asyncio.sleep(_retry_after_seconds(e) + 1)
            try:
                return await send_fn(plain_text, **send_kwargs)
            except TelegramError as e2:
                if is_thread_gone(e2):
                    return None
                last_error = e2
        except TelegramError as e:
            if is_thread_gone(e):
                return None
            last_error = e

    if last_error is not None:
        logger.warning("Failed to %s: %s", context_label, last_error)
    return None


async def _send_with_fallback(
    client: TelegramClient,
    chat_id: int,
    text: str,
    **kwargs: Any,
) -> Message | None:
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)

    async def _send(text: str, **kw: Any) -> Message:
        return await client.send_message(chat_id=chat_id, text=text, **kw)

    return await _with_entity_fallback(
        _send, text, f"send message to {chat_id}", **kwargs
    )


async def rate_limit_send_message(
    client: TelegramClient,
    chat_id: int,
    text: str,
    **kwargs: Any,
) -> Message | None:
    started_at = time.monotonic()
    await rate_limit_send(chat_id)
    sent = await _send_with_fallback(client, chat_id, text, **kwargs)
    elapsed_secs = time.monotonic() - started_at
    if config.diagnostic_logs and elapsed_secs >= _SEND_WARN_SECS:
        logger.warning(
            "telegram_send_slow",
            chat_id=chat_id,
            message_thread_id=kwargs.get("message_thread_id"),
            text_len=len(text),
            elapsed_ms=int(elapsed_secs * 1000),
            sent=sent is not None,
        )
    return sent


async def safe_reply(message: Message, text: str, **kwargs: Any) -> Message | None:
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)

    async def _reply(text: str, **kw: Any) -> Message:
        try:
            return await message.reply_text(text, **kw)
        except BadRequest as exc:
            if "not found" in str(exc).lower():
                logger.warning("Cannot reply: original message gone (%s)", exc)
                raise _MessageGoneError from exc
            raise

    try:
        return await _with_entity_fallback(_reply, text, "reply", **kwargs)
    except _MessageGoneError:
        return None


async def safe_edit(target: Message | CallbackQuery, text: str, **kwargs: Any) -> None:
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    raw_edit_fn = (
        target.edit_text if isinstance(target, Message) else target.edit_message_text
    )

    async def _edit(text: str, **kw: Any) -> Any:
        return await raw_edit_fn(text, **kw)

    await _with_entity_fallback(_edit, text, "edit message", **kwargs)


async def safe_send(
    client: TelegramClient,
    chat_id: int,
    text: str,
    message_thread_id: int | None = None,
    **kwargs: Any,
) -> Message | None:
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    if message_thread_id is not None:
        kwargs.setdefault("message_thread_id", message_thread_id)

    async def _send(text: str, **kw: Any) -> Message:
        return await client.send_message(chat_id=chat_id, text=text, **kw)

    return await _with_entity_fallback(
        _send, text, f"send message to {chat_id}", **kwargs
    )


async def edit_with_fallback(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    text: str,
    **kwargs: Any,
) -> bool:
    kwargs.setdefault("link_preview_options", NO_LINK_PREVIEW)
    plain_text, entities = convert_to_entities(text)

    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=plain_text,
            entities=entities,
            **kwargs,
        )
        return True
    except RetryAfter:
        raise
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return True
        return await _retry_edit_plain(client, chat_id, message_id, plain_text, kwargs)
    except TelegramError:
        return await _retry_edit_plain(client, chat_id, message_id, plain_text, kwargs)


async def _retry_edit_plain(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    plain_text: str,
    kwargs: dict[str, Any],
) -> bool:
    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=plain_text,
            **kwargs,
        )
        return True
    except RetryAfter:
        raise
    except TelegramError:
        return False


async def ack_reaction(client: TelegramClient, chat_id: int, message_id: int) -> None:
    if not config.ack_reaction:
        return
    with contextlib.suppress(TelegramError):
        await client.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=config.ack_reaction)],
        )


def send_kwargs(thread_id: int | None) -> dict[str, Any]:
    if thread_id is not None:
        return {"message_thread_id": thread_id}
    return {}
