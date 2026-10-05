from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING

import structlog
from telegram.error import TelegramError

from .cc_commands import register_commands
from .config import config
from .handlers.commands import setup_menu_refresh_job
from .handlers.hook_events import dispatch_hook_event
from .handlers.messaging_pipeline.message_queue import shutdown_workers
from .handlers.messaging_pipeline.message_routing import handle_new_message
from .handlers.polling.polling_coordinator import status_poll_loop
from .handlers.shell import register_approval_callback, show_command_approval
from .handlers.topics.topic_orchestration import (
    adopt_unbound_windows as _adopt_unbound_windows,
)
from .handlers.topics.topic_orchestration import (
    handle_new_window as _handle_new_window,
)
from .multiplexer import get_multiplexer, install_multiplexer, multiplexer
from .providers import get_provider
from .session import session_manager
from .telegram_client import PTBTelegramClient
from .session_monitor import (
    NewMessage,
    NewWindowEvent,
    SessionMonitor,
    clear_active_monitor,
    set_active_monitor,
)
from .utils import task_done_callback

if TYPE_CHECKING:
    from telegram.ext import Application

    from .providers.base import HookEvent

logger = structlog.get_logger()

session_monitor: SessionMonitor | None = None
_status_poll_task: asyncio.Task[None] | None = None
_callbacks_wired = False


def install_global_exception_handler() -> None:
    asyncio.get_running_loop().set_exception_handler(_global_exception_handler)


def _global_exception_handler(
    _loop: asyncio.AbstractEventLoop, ctx: dict[str, object]
) -> None:
    exc = ctx.get("exception")
    msg = ctx.get("message", "Unhandled exception in event loop")
    if isinstance(exc, BaseException):
        logger.error(
            "asyncio exception handler: %s",
            msg,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    else:
        logger.error("asyncio exception handler: %s", msg)


async def register_provider_commands(application: Application) -> None:
    default_provider = get_provider()
    try:
        await register_commands(application.bot, provider=default_provider)
    except TelegramError:
        logger.warning("Failed to register bot commands at startup, will retry later")
    setup_menu_refresh_job(application)


def verify_hooks_installed() -> None:
    provider = get_provider()
    if not provider.capabilities.supports_hook:
        return
    provider_name = provider.capabilities.name
    if provider_name != "claude":
        if provider.capabilities.hook_install_managed_by_ccgram:
            logger.debug(
                "%s hooks can improve status tracking. Run: ccgram hook --provider %s --install",
                provider_name,
                provider_name,
            )
        return

    from .hook import _claude_settings_file, get_installed_events

    settings_file = _claude_settings_file()
    if not settings_file.exists():
        logger.warning(
            "Claude Code hooks not installed (%s missing). Run: ccgram hook --install",
            settings_file,
        )
        return

    try:
        settings = json.loads(settings_file.read_text())
    except json.JSONDecodeError, OSError:
        logger.warning("Claude Code hooks not installed. Run: ccgram hook --install")
        return

    events = get_installed_events(settings)
    missing = [e for e, ok in events.items() if not ok]
    if missing:
        logger.warning(
            "Claude Code hooks incomplete — %d missing: %s. Run: ccgram hook --install",
            len(missing),
            ", ".join(missing),
        )


def wire_multiplexer() -> None:
    backend = get_multiplexer(config.multiplexer_name)
    install_multiplexer(backend)
    logger.info("Multiplexer backend wired: %s", backend.capabilities.name)


async def ensure_multiplexer_session() -> None:
    try:
        await multiplexer.ensure_session()
    except Exception as exc:
        logger.error(
            "Multiplexer '%s' is not available: %s. "
            "Make sure it is installed and running, then start ccgram again.",
            config.multiplexer_name,
            exc,
        )
        raise SystemExit(1) from exc


def wire_runtime_callbacks() -> None:
    global _callbacks_wired

    if _callbacks_wired:
        return

    register_approval_callback(show_command_approval)
    _callbacks_wired = True


async def start_session_monitor(application: Application) -> SessionMonitor:
    global session_monitor

    if not _callbacks_wired:
        raise RuntimeError(
            "wire_runtime_callbacks() must run before start_session_monitor()"
        )

    monitor = SessionMonitor()
    set_active_monitor(monitor)

    client = PTBTelegramClient(application.bot)

    async def message_callback(msg: NewMessage) -> None:
        await handle_new_message(msg, client)

    monitor.set_message_callback(message_callback)

    async def new_window_callback(event: NewWindowEvent) -> None:
        await _handle_new_window(event, client)

    monitor.set_new_window_callback(new_window_callback)

    async def hook_event_callback(event: HookEvent) -> None:
        await dispatch_hook_event(event, client)

    monitor.set_hook_event_callback(hook_event_callback)

    monitor.start()
    session_monitor = monitor
    logger.info("Session monitor started")
    return monitor


def start_status_polling(application: Application) -> asyncio.Task[None]:
    global _status_poll_task

    _status_poll_task = asyncio.create_task(status_poll_loop(application.bot))
    _status_poll_task.add_done_callback(task_done_callback)
    logger.info("Status polling task started")
    return _status_poll_task


def start_event_stream(application: Application) -> object | None:
    if not multiplexer.capabilities.supports_event_stream:
        return None
    from .event_stream_monitor import EventStreamMonitor, set_active_event_stream

    from .thread_router import thread_router

    def _bound_window_ids() -> set[str]:
        return {wid for _u, _t, wid in thread_router.iter_thread_bindings()}

    monitor = EventStreamMonitor(PTBTelegramClient(application.bot), _bound_window_ids)
    monitor.start()
    set_active_event_stream(monitor)
    logger.info("Event-stream consumer started")
    return monitor


async def bootstrap_application(application: Application) -> None:
    install_global_exception_handler()
    wire_multiplexer()
    await ensure_multiplexer_session()
    await register_provider_commands(application)
    await session_manager.resolve_stale_ids()
    await _adopt_unbound_windows(PTBTelegramClient(application.bot))
    verify_hooks_installed()
    wire_runtime_callbacks()
    await start_session_monitor(application)
    start_status_polling(application)
    start_event_stream(application)

    from .main import start_miniapp_if_enabled

    await start_miniapp_if_enabled()


async def shutdown_runtime() -> None:
    global _status_poll_task, session_monitor

    if _status_poll_task is not None:
        _status_poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _status_poll_task
        _status_poll_task = None
        logger.info("Status polling stopped")

    if session_monitor is not None:
        session_monitor.stop()
        logger.info("Session monitor stopped")
        session_monitor = None
    clear_active_monitor()

    from .event_stream_monitor import get_active_event_stream, set_active_event_stream

    event_stream = get_active_event_stream()
    if event_stream is not None:
        event_stream.stop()
        set_active_event_stream(None)
        logger.info("Event-stream consumer stopped")

    await shutdown_workers()

    from .main import stop_miniapp_if_enabled

    await stop_miniapp_if_enabled()

    session_manager.flush_state()


def reset_for_testing() -> None:
    global _callbacks_wired, session_monitor, _status_poll_task

    from .handlers.shell import shell_capture

    shell_capture._reset_approval_callback_for_testing()

    _callbacks_wired = False
    session_monitor = None
    _status_poll_task = None
    clear_active_monitor()

    from .event_stream_monitor import get_active_event_stream, set_active_event_stream
    from .multiplexer import agent_status_cache

    event_stream = get_active_event_stream()
    if event_stream is not None:
        event_stream.stop()
        set_active_event_stream(None)
    agent_status_cache.reset()
