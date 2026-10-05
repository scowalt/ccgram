from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

import structlog

from .multiplexer import agent_status_cache
from .multiplexer import multiplexer as mux
from .multiplexer.base import MuxEvent
from .telegram_client import TelegramClient, unwrap_bot

logger = structlog.get_logger()

_SET_POLL_INTERVAL = 2.0

WindowIdsProvider = Callable[[], set[str]]


class EventStreamMonitor:
    def __init__(
        self, client: TelegramClient, list_window_ids: WindowIdsProvider
    ) -> None:
        self._client = client
        self._list_window_ids = list_window_ids
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._supervise())

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None
        agent_status_cache.reset()

    async def _supervise(self) -> None:
        while self._running:
            ids = self._list_window_ids()
            if not ids:
                await asyncio.sleep(_SET_POLL_INTERVAL)
                continue
            try:
                await self._run_until_set_change(ids)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("event stream supervisor error: %s", exc)
                await asyncio.sleep(_SET_POLL_INTERVAL)

    async def _run_until_set_change(self, ids: set[str]) -> None:
        consume = asyncio.create_task(self._consume(ids))
        try:
            while self._running and not consume.done():
                if self._list_window_ids() != ids:
                    return
                await asyncio.sleep(_SET_POLL_INTERVAL)
        finally:
            if not consume.done():
                consume.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await consume
        if not consume.cancelled() and (exc := consume.exception()) is not None:
            raise exc

    async def _consume(self, ids: set[str]) -> None:
        async with contextlib.aclosing(mux.watch_events(sorted(ids))) as stream:
            async for event in stream:
                try:
                    await self._dispatch(event)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("event-stream dispatch failed: %s", exc)

    async def _dispatch(self, event: MuxEvent) -> None:
        if event.kind == "agent_status" and event.status is not None:
            agent_status_cache.set_status(event.window_id, event.status)
        elif event.kind == "window_died":
            agent_status_cache.clear(event.window_id)
            await self._notify_dead(event.window_id)

    async def _notify_dead(self, window_id: str) -> None:
        from .handlers.polling.window_tick.apply import (
            _handle_dead_window_notification,
        )

        from .thread_router import thread_router

        bot = unwrap_bot(self._client)
        for user_id, thread_id, bound_wid in thread_router.iter_thread_bindings():
            if bound_wid == window_id:
                await _handle_dead_window_notification(
                    bot, user_id, thread_id, window_id
                )


_active: EventStreamMonitor | None = None


def set_active_event_stream(monitor: EventStreamMonitor | None) -> None:
    global _active
    _active = monitor


def get_active_event_stream() -> EventStreamMonitor | None:
    return _active
