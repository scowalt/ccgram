from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

import structlog
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut

from ... import window_query
from ...config import config
from ...providers import (
    detect_provider_from_pane,
    detect_provider_from_runtime,
    should_probe_pane_title_for_provider_detection,
)
from ...session import session_manager
from ...session_monitor import NewWindowEvent
from ...telegram_client import TelegramClient
from ...thread_router import thread_router
from ...multiplexer import multiplexer as tmux_manager
from ..messaging_pipeline.message_sender import is_thread_gone
from ..status.topic_emoji import strip_emoji_prefix

logger = structlog.get_logger()

_topic_create_retry_until: dict[int, float] = {}
_TOPIC_CREATE_RETRY_BUFFER_SECONDS = 1

_TOPIC_CREATE_TRANSIENT_RETRIES = 1
_TOPIC_CREATE_TRANSIENT_BACKOFF_S = 1.0

_topic_create_failed_windows: set[str] = set()


async def _create_forum_topic_with_retry(
    client: TelegramClient, chat_id: int, topic_name: str
):
    last_exc: TelegramError | None = None
    for attempt in range(_TOPIC_CREATE_TRANSIENT_RETRIES + 1):
        try:
            return await client.create_forum_topic(chat_id=chat_id, name=topic_name)
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
            if attempt < _TOPIC_CREATE_TRANSIENT_RETRIES:
                logger.debug("create_forum_topic transient error, retrying: %s", exc)
                await asyncio.sleep(_TOPIC_CREATE_TRANSIENT_BACKOFF_S)
                continue
            raise
    assert last_exc is not None
    raise last_exc


def clear_topic_create_retry(chat_id: int, _thread_id: int = 0) -> None:
    _topic_create_retry_until.pop(chat_id, None)


def _is_window_already_bound(window_id: str) -> bool:
    return thread_router.has_window(window_id)


_pending_user_creations: dict[str, float] = {}
_PENDING_CREATION_TTL_S = 30.0


def register_pending_creation(window_id: str) -> None:
    if not window_id:
        return
    _pending_user_creations[window_id] = time.monotonic() + _PENDING_CREATION_TTL_S


def clear_pending_creation(window_id: str) -> None:
    _pending_user_creations.pop(window_id, None)


def _is_pending_user_creation(window_id: str) -> bool:
    expires_at = _pending_user_creations.get(window_id)
    if expires_at is None:
        return False
    if time.monotonic() >= expires_at:
        _pending_user_creations.pop(window_id, None)
        return False
    return True


async def _auto_detect_provider(window_id: str) -> None:
    view = window_query.view_window(window_id)
    if view and view.provider_name:
        return

    w = await tmux_manager.find_window_by_id(window_id)
    if not w or not w.pane_current_command:
        return

    detected = await detect_provider_from_pane(
        w.pane_current_command,
        window_id=window_id,
    )
    if not detected and should_probe_pane_title_for_provider_detection(
        w.pane_current_command
    ):
        pane_title = await tmux_manager.get_pane_title(window_id)
        detected = detect_provider_from_runtime(
            w.pane_current_command,
            pane_title=pane_title,
        )
    if detected:
        session_manager.set_window_provider(window_id, detected, cwd=w.cwd or None)
        logger.info(
            "Auto-detected provider %r for window %s (command=%s)",
            detected,
            window_id,
            w.pane_current_command,
        )


def collect_target_chats(window_id: str) -> set[int]:
    seen_chats: set[int] = set()
    for user_id, thread_id, _ in thread_router.iter_thread_bindings():
        chat_id = thread_router.resolve_chat_id(user_id, thread_id)
        if isinstance(chat_id, int) and chat_id < 0:
            seen_chats.add(chat_id)

    if not seen_chats:
        seen_chats.update(
            cid for cid in thread_router.group_chat_ids.values() if cid < 0
        )

    if not seen_chats:
        if config.group_id:
            seen_chats.add(config.group_id)
            logger.info(
                "Cold-start: using CCGRAM_GROUP_ID=%d for auto-topic (window %s)",
                config.group_id,
                window_id,
            )
        else:
            logger.debug(
                "No group chats found for auto-topic creation (window %s)",
                window_id,
            )

    return seen_chats


def _bind_topic_to_user(
    thread_id: int, window_id: str, chat_id: int, topic_name: str
) -> None:
    for user_id, tid, _ in thread_router.iter_thread_bindings():
        if thread_router.resolve_chat_id(user_id, tid) == chat_id:
            thread_router.bind_thread(
                user_id, thread_id, window_id, window_name=topic_name
            )
            thread_router.set_group_chat_id(user_id, thread_id, chat_id)
            return

    if config.allowed_users:
        first_user_id = next(iter(config.allowed_users))
        thread_router.bind_thread(
            first_user_id, thread_id, window_id, window_name=topic_name
        )
        thread_router.set_group_chat_id(first_user_id, thread_id, chat_id)


async def create_topic_in_chat(
    client: TelegramClient, chat_id: int, window_id: str, topic_name: str
) -> None:
    retry_until = _topic_create_retry_until.get(chat_id, 0.0)
    now = time.monotonic()
    if now < retry_until:
        wait_seconds = max(1, int(retry_until - now))
        logger.debug(
            "Skipping auto-topic creation for chat %d (window %s), "
            "backoff active for %ss",
            chat_id,
            window_id,
            wait_seconds,
        )
        return

    try:
        topic = await _create_forum_topic_with_retry(client, chat_id, topic_name)
        _topic_create_retry_until.pop(chat_id, None)
        logger.info(
            "Auto-created topic '%s' (thread=%d) in chat %d for window %s",
            topic_name,
            topic.message_thread_id,
            chat_id,
            window_id,
        )
        _bind_topic_to_user(topic.message_thread_id, window_id, chat_id, topic_name)
    except RetryAfter as e:
        retry_after_seconds = (
            e.retry_after
            if isinstance(e.retry_after, int)
            else int(e.retry_after.total_seconds())
        )
        retry_after_seconds = max(1, retry_after_seconds)
        _topic_create_retry_until[chat_id] = (
            time.monotonic() + retry_after_seconds + _TOPIC_CREATE_RETRY_BUFFER_SECONDS
        )
        logger.warning(
            "Flood control creating topic for window %s in chat %d, backing off %ss",
            window_id,
            chat_id,
            retry_after_seconds,
        )
    except (TimedOut, NetworkError) as exc:
        _topic_create_retry_until[chat_id] = (
            time.monotonic()
            + _TOPIC_CREATE_TRANSIENT_BACKOFF_S
            + _TOPIC_CREATE_RETRY_BUFFER_SECONDS
        )
        logger.warning(
            "Transient failure creating topic for window %s in chat %d; will retry: %s",
            window_id,
            chat_id,
            exc,
        )
    except TelegramError:
        logger.exception(
            "Failed to create topic for window %s in chat %d",
            window_id,
            chat_id,
        )
        _topic_create_failed_windows.add(window_id)


async def _topic_exists(
    client: TelegramClient, chat_id: int, thread_id: int
) -> bool | None:
    try:
        msg = await client.send_message(
            chat_id,
            "​",
            message_thread_id=thread_id,
            disable_notification=True,
        )
    except TelegramError as exc:
        if is_thread_gone(exc):
            return False
        return None
    with contextlib.suppress(TelegramError):
        await client.delete_message(chat_id, msg.message_id)
    return True


async def _rebind_existing_topic_by_name(
    event: NewWindowEvent, client: TelegramClient, topic_name: str
) -> bool:
    clean_topic_name = strip_emoji_prefix(topic_name)
    matches: list[tuple[int, int, str, int]] = []
    bindings = list(thread_router.iter_thread_bindings())
    for user_id, thread_id, old_window_id in bindings:
        if old_window_id == event.window_id:
            continue
        display_name = strip_emoji_prefix(thread_router.get_display_name(old_window_id))
        if display_name != clean_topic_name:
            continue
        if await tmux_manager.find_window_by_id(old_window_id):
            continue
        chat_id = thread_router.resolve_chat_id(user_id, thread_id)
        if chat_id == user_id:
            continue
        matches.append((user_id, thread_id, old_window_id, chat_id))

    if len(matches) != 1:
        if len(matches) > 1:
            logger.warning(
                "Multiple stale same-name topics for window %s (%s); not rebinding",
                event.window_id,
                clean_topic_name,
            )
        return False

    user_id, thread_id, old_window_id, chat_id = matches[0]
    exists = await _topic_exists(client, chat_id, thread_id)
    if exists is False:
        thread_router.unbind_thread(user_id, thread_id)
        logger.info(
            "Dropped dead same-name topic thread %d for stale window %s",
            thread_id,
            old_window_id,
        )
        return False
    if exists is None:
        logger.info(
            "Could not probe same-name topic thread %d for stale window %s; not rebinding",
            thread_id,
            old_window_id,
        )
        return False

    thread_router.bind_thread(
        user_id, thread_id, event.window_id, window_name=topic_name
    )
    thread_router.set_group_chat_id(user_id, thread_id, chat_id)
    logger.info(
        "Rebound existing topic thread %d from stale window %s to new window %s (%s)",
        thread_id,
        old_window_id,
        event.window_id,
        clean_topic_name,
    )
    return True


async def handle_new_window(event: NewWindowEvent, client: TelegramClient) -> None:
    if _is_window_already_bound(event.window_id):
        logger.debug(
            "New window %s already bound, skipping topic creation", event.window_id
        )
        return

    if _is_pending_user_creation(event.window_id):
        logger.debug(
            "New window %s creation pending in directory flow — "
            "skipping auto topic creation",
            event.window_id,
        )
        return

    if event.window_id in _topic_create_failed_windows:
        return

    await _auto_detect_provider(event.window_id)

    topic_name = event.window_name or Path(event.cwd).name or event.window_id
    if await _rebind_existing_topic_by_name(event, client, topic_name):
        return

    seen_chats = collect_target_chats(event.window_id)
    if not seen_chats:
        return

    for chat_id in seen_chats:
        await create_topic_in_chat(client, chat_id, event.window_id, topic_name)


async def adopt_unbound_windows(client: TelegramClient) -> None:
    all_windows = await tmux_manager.list_windows()
    live_ids = {w.window_id for w in all_windows}
    live_pairs = [(w.window_id, w.window_name) for w in all_windows]
    audit = session_manager.audit_state(live_ids, live_pairs)
    orphaned = [i for i in audit.issues if i.category == "orphaned_window"]
    if orphaned:
        from ..sync_command import _adopt_orphaned_windows

        await _adopt_orphaned_windows(client, orphaned)
        logger.info("Startup: adopted %d unbound window(s)", len(orphaned))
