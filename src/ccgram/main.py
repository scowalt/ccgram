import asyncio
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from aiohttp import web

_restart_requested = False

_shutdown_signal = 0

_miniapp_runner: "web.AppRunner | None" = None


def _on_signal(signum: int) -> None:
    global _shutdown_signal
    _shutdown_signal = signum
    sig_name = signal.Signals(signum).name
    sys.stderr.write(f"\n[ccgram] {sig_name} received (pid={os.getpid()})\n")
    sys.stderr.flush()
    raise SystemExit


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGQUIT):
        loop.add_signal_handler(sig, _on_signal, sig)


def _reraise_shutdown_signal() -> None:
    if _shutdown_signal:
        signal.signal(_shutdown_signal, signal.SIG_DFL)
        os.kill(os.getpid(), _shutdown_signal)


_GRAY_ON = "\x1b[90m"
_GRAY_OFF = "\x1b[39m"
_DEBUG_RESERVED_KEYS = frozenset(
    {"event", "level", "timestamp", "logger", "logger_name"}
)


def _dim_debug_event(
    _logger: object, _method: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    if event_dict.get("level") != "debug":
        return event_dict
    event = event_dict.pop("event", "")
    parts = [str(event)] if event else []
    for key in list(event_dict):
        if key in _DEBUG_RESERVED_KEYS:
            continue
        parts.append(f"{key}={event_dict.pop(key)}")
    event_dict["event"] = f"{_GRAY_ON}{' '.join(parts)}{_GRAY_OFF}"
    return event_dict


def _use_colors(stream: object) -> bool:
    if "NO_COLOR" in os.environ:
        return False
    if "FORCE_COLOR" in os.environ:
        return True
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _log_level_styles() -> dict[str, str]:
    styles = structlog.dev.ConsoleRenderer.get_default_level_styles().copy()
    styles.update(
        {
            "debug": _GRAY_ON,
            "info": "\x1b[32m",
            "warning": "\x1b[1;33m",
            "warn": "\x1b[1;33m",
            "error": "\x1b[1;31m",
            "exception": "\x1b[1;31m",
            "critical": "\x1b[1;91m",
        }
    )
    return styles


def setup_logging(log_level: str) -> None:
    numeric_level = getattr(logging, log_level, None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    level_styles = _log_level_styles()
    stdout_colors = _use_colors(sys.stdout)
    stderr_colors = _use_colors(sys.stderr)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _dim_debug_event,
            structlog.dev.ConsoleRenderer(
                colors=stdout_colors,
                pad_event=40,
                level_styles=level_styles if stdout_colors else None,
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(
                colors=stderr_colors,
                level_styles=level_styles if stderr_colors else None,
            ),
            foreign_pre_chain=[
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            ],
        )
    )
    root.addHandler(handler)
    root.setLevel(logging.WARNING)

    logging.getLogger("ccgram").setLevel(numeric_level)
    for name in ("httpx", "httpcore", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _ensure_tmux_session(auto_detected: bool) -> None:
    from .config import config

    from .multiplexer.tmux import tmux_manager

    logger = structlog.get_logger()

    if auto_detected:
        session = tmux_manager.get_session()
        if not session:
            logger.error("Tmux session '%s' not found", config.tmux_session_name)
            sys.exit(1)
        logger.info("Using auto-detected tmux session '%s'", session.session_name)
        return

    try:
        session = tmux_manager.get_or_create_session()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "tmux is not available: %s. Install and start tmux, then run ccgram again.",
            exc,
        )
        sys.exit(1)
    logger.info("Tmux session '%s' ready", session.session_name)


def run_bot() -> None:
    log_level = (os.environ.get("CCGRAM_LOG_LEVEL") or "INFO").upper()
    setup_logging(log_level)

    explicit_session = os.environ.get("TMUX_SESSION_NAME")
    auto_detected = False

    if not explicit_session and os.environ.get("TMUX"):
        from .utils import check_duplicate_ccgram, detect_tmux_context

        detected, own_wid = detect_tmux_context()
        if detected:
            os.environ["TMUX_SESSION_NAME"] = detected
            auto_detected = True

        dup = check_duplicate_ccgram(detected or "ccgram")
        if dup:
            print(f"Error: {dup}", file=sys.stderr)
            sys.exit(1)
    else:
        own_wid = None

    try:
        from .config import config
    except ValueError as e:
        from .utils import ccgram_dir

        config_dir = ccgram_dir()
        env_path = config_dir / ".env"
        print(f"Error: {e}\n")
        print(f"Create {env_path} with the following content:\n")
        print("  TELEGRAM_BOT_TOKEN=your_bot_token_here")
        print("  ALLOWED_USERS=your_telegram_user_id")
        print()
        print("Get your bot token from @BotFather on Telegram.")
        print("Get your user ID from @userinfobot on Telegram.")
        sys.exit(1)

    if own_wid:
        config.own_window_id = own_wid

    logger = structlog.get_logger()

    logger.info("Allowed users: %d configured", len(config.allowed_users))
    logger.info("Claude projects path: %s", config.claude_projects_path)

    if config.multiplexer_name == "tmux":
        _ensure_tmux_session(auto_detected)

    from . import __version__

    dev = "+dev" if "+unknown" in __version__ or ".dev" in __version__ else ""
    logger.info("Starting ccgram %s%s", __version__, dev)
    from .bot import create_bot

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = create_bot()
    _install_signal_handlers(loop)
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        stop_signals=None,
    )

    if _restart_requested:
        logger.info("Restarting bot via os.execv(%s)", sys.argv)
        os.execv(sys.argv[0], sys.argv)

    _reraise_shutdown_signal()


async def start_miniapp_if_enabled() -> None:
    global _miniapp_runner

    if _miniapp_runner is not None:
        return

    from .config import config

    if not config.miniapp_base_url:
        return

    logger = structlog.get_logger()
    try:
        from .miniapp import start_server

        _miniapp_runner = await start_server(
            bot_token=config.telegram_bot_token,
            host=config.miniapp_host,
            port=config.miniapp_port,
        )
        logger.info(
            "Mini App server started: base_url=%s host=%s port=%d",
            config.miniapp_base_url,
            config.miniapp_host,
            config.miniapp_port,
        )
    except OSError as exc:
        logger.error("Mini App server failed to bind: %s", exc)
        _miniapp_runner = None


async def stop_miniapp_if_enabled() -> None:
    global _miniapp_runner

    if _miniapp_runner is None:
        return

    logger = structlog.get_logger()
    try:
        from .miniapp import stop_server

        await stop_server(_miniapp_runner)
    except OSError as exc:
        logger.warning("Mini App server stop raised: %s", exc)
    finally:
        _miniapp_runner = None


def main() -> None:
    from .cli import cli

    cli()


if __name__ == "__main__":
    main()
