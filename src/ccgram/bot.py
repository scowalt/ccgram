import os
import signal

import structlog
from telegram.error import BadRequest, Conflict, NetworkError
from telegram.ext import (
    AIORateLimiter,
    Application,
    ContextTypes,
    filters,
)

from . import bootstrap
from .config import config
from .handlers.inline import inline_query_handler, unsupported_content_handler
from .handlers.commands import commands_command, toolbar_command
from .handlers.messaging_pipeline import toolcalls_command, verbose_command
from .handlers.messaging_pipeline.message_sender import safe_reply
from .handlers.recovery.history import history_command
from .handlers.registry import register_all
from .handlers.text.text_handler import handle_text_message, text_handler
from .handlers.topics import new_command
from .handlers.topics.directory_browser import clear_browse_state
from .session import session_manager
from .telegram_request import (
    ResilientPollingHTTPXRequest,
    telegram_transport_failed_recently,
)
from .thread_router import thread_router

__all__ = [
    "clear_browse_state",
    "commands_command",
    "create_bot",
    "handle_text_message",
    "history_command",
    "inline_query_handler",
    "is_user_allowed",
    "new_command",
    "post_init",
    "post_shutdown",
    "post_stop",
    "safe_reply",
    "session_manager",
    "text_handler",
    "thread_router",
    "toolbar_command",
    "toolcalls_command",
    "unsupported_content_handler",
    "verbose_command",
]

logger = structlog.get_logger()


def is_user_allowed(user_id: int | None) -> bool:
    return user_id is not None and config.is_user_allowed(user_id)


_group_filter: filters.BaseFilter = (
    filters.Chat(chat_id=config.group_id) if config.group_id else filters.ALL
)


async def post_init(application: Application) -> None:
    await bootstrap.bootstrap_application(application)


async def _send_shutdown_notification(application: Application) -> None:
    from .main import _shutdown_signal

    if not config.group_id:
        return

    sig = _shutdown_signal
    reason = f"Received {signal.Signals(sig).name}" if sig else "Clean exit"

    from . import __version__

    from telegram.error import TelegramError

    text = f"🔌 ccgram stopped — {reason} (v{__version__})"
    try:
        await application.bot.send_message(
            chat_id=config.group_id,
            text=text,
            message_thread_id=1,
        )
    except (TelegramError, RuntimeError) as exc:
        logger.debug("Shutdown notification skipped: %s", exc)


async def post_stop(application: Application) -> None:
    await _send_shutdown_notification(application)


async def post_shutdown(_application: Application) -> None:
    await bootstrap.shutdown_runtime()


async def _error_handler(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        if telegram_transport_failed_recently():
            logger.warning(
                "Telegram polling conflict after a recent transport failure; "
                "treating as transient self-conflict and letting PTB retry: %s",
                context.error,
            )
            return
        logger.critical(
            "Another bot instance is polling with the same token. "
            "Shutting down to avoid conflicts."
        )
        os.kill(os.getpid(), signal.SIGINT)
        return
    if isinstance(context.error, BadRequest) and "too old" in str(context.error):
        logger.debug("Callback query expired (query too old)")
        return
    if isinstance(context.error, NetworkError) and not isinstance(
        context.error, BadRequest
    ):
        logger.info("Transient network error (PTB will retry): %s", context.error)
        return
    logger.error("Unhandled bot error", exc_info=context.error)


def create_bot() -> Application:
    import warnings

    warnings.filterwarnings("ignore", message=".*JobQueue.*", category=UserWarning)
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .rate_limiter(AIORateLimiter(max_retries=5))
        .request(ResilientPollingHTTPXRequest())
        .get_updates_request(
            ResilientPollingHTTPXRequest(
                connection_pool_size=1,
                track_polling_failures=True,
            )
        )
        .post_init(post_init)
        .post_stop(post_stop)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_error_handler(_error_handler)
    register_all(application, _group_filter)

    return application
