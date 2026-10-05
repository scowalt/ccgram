import asyncio
import contextlib
import time

import structlog

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut

from ...providers import get_provider_for_window
from ...telegram_client import TelegramClient
from ...window_query import get_window_provider
from ...thread_router import thread_router
from ...multiplexer import multiplexer as tmux_manager
from ...topic_state_registry import topic_state
from ..callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_REFRESH,
    CB_ASK_RIGHT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
)
from ..messaging_pipeline.message_sender import (
    NO_LINK_PREVIEW,
    is_thread_gone,
    rate_limit_send,
)

logger = structlog.get_logger()

INTERACTIVE_TOOL_NAMES = frozenset(
    {
        "AskUserQuestion",
        "ExitPlanMode",
        "request_user_input",
    }
)

_interactive_msgs: dict[tuple[int, int], int] = {}

_interactive_mode: dict[tuple[int, int], str] = {}

_send_cooldowns: dict[tuple[int, int], float] = {}
_SEND_RETRY_INTERVAL = 5.0
_DEAD_TOPIC_RETRY_INTERVAL = 60.0

_INTERACTIVE_SEND_RETRIES = 1
_INTERACTIVE_SEND_RETRY_BACKOFF_S = 1.0

INTERACTIVE_INSTRUCTION_LINE = (
    "↑↓ select · Enter confirm · Esc cancel · type to enter text"
)

_TELEGRAM_MAX_TEXT = 4096


def format_interactive_message(
    text: str,
    pane_id: str | None = None,
    pane_name: str | None = None,
) -> str:
    header = INTERACTIVE_INSTRUCTION_LINE
    if pane_id:
        label = pane_name.strip() if pane_name and pane_name.strip() else "Pane"
        header = f"{header}\n\U0001f500 {label} ({pane_id}):"

    body = text
    overhead = len(header) + 1
    if overhead + len(body) > _TELEGRAM_MAX_TEXT:
        budget = _TELEGRAM_MAX_TEXT - overhead
        body = body[-budget:] if budget > 0 else ""
    return f"{header}\n{body}"


@topic_state.register("topic")
def clear_send_cooldowns(user_id: int, thread_id: int) -> None:
    _send_cooldowns.pop((user_id, thread_id or 0), None)


def get_interactive_window(user_id: int, thread_id: int | None = None) -> str | None:
    return _interactive_mode.get((user_id, thread_id or 0))


def set_interactive_mode(
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
) -> None:
    logger.debug(
        "Set interactive mode: user=%d, window_id=%s, thread=%s",
        user_id,
        window_id,
        thread_id,
    )
    _interactive_mode[(user_id, thread_id or 0)] = window_id


def clear_interactive_mode(user_id: int, thread_id: int | None = None) -> None:
    logger.debug("Clear interactive mode: user=%d, thread=%s", user_id, thread_id)
    _interactive_mode.pop((user_id, thread_id or 0), None)


def get_interactive_msg_id(user_id: int, thread_id: int | None = None) -> int | None:
    return _interactive_msgs.get((user_id, thread_id or 0))


def _build_interactive_keyboard(
    window_id: str,
    ui_name: str = "",
    pane_id: str | None = None,
) -> InlineKeyboardMarkup:
    from ..callback_data import CB_PANE_DELIMITER

    vertical_only = ui_name == "RestoreCheckpoint"
    target = f"{window_id}{CB_PANE_DELIMITER}{pane_id}" if pane_id else window_id

    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [
            InlineKeyboardButton(
                "␣ Space", callback_data=f"{CB_ASK_SPACE}{target}"[:64]
            ),
            InlineKeyboardButton("↑", callback_data=f"{CB_ASK_UP}{target}"[:64]),
            InlineKeyboardButton("⇥ Tab", callback_data=f"{CB_ASK_TAB}{target}"[:64]),
        ]
    )
    if vertical_only:
        rows.append(
            [
                InlineKeyboardButton("↓", callback_data=f"{CB_ASK_DOWN}{target}"[:64]),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton("←", callback_data=f"{CB_ASK_LEFT}{target}"[:64]),
                InlineKeyboardButton("↓", callback_data=f"{CB_ASK_DOWN}{target}"[:64]),
                InlineKeyboardButton("→", callback_data=f"{CB_ASK_RIGHT}{target}"[:64]),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("⎋ Esc", callback_data=f"{CB_ASK_ESC}{target}"[:64]),
            InlineKeyboardButton("🔄", callback_data=f"{CB_ASK_REFRESH}{target}"[:64]),
            InlineKeyboardButton(
                "⏎ Enter", callback_data=f"{CB_ASK_ENTER}{target}"[:64]
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _edit_interactive_msg(
    client: TelegramClient,
    chat_id: int,
    msg_id: int,
    text: str,
    keyboard: InlineKeyboardMarkup,
    ikey: tuple[int, int],
    window_id: str,
) -> bool | None:
    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            reply_markup=keyboard,
            link_preview_options=NO_LINK_PREVIEW,
        )
        _interactive_mode[ikey] = window_id
        return True
    except BadRequest as e:
        if "Message is not modified" in e.message:
            return True
        logger.warning("BadRequest editing interactive msg: %s", e.message)
        return False
    except RetryAfter:
        raise
    except TelegramError:
        logger.warning("Failed to edit interactive message", exc_info=True)
        return False


async def _capture_interactive_content(
    window_id: str,
    pane_id: str | None = None,
) -> tuple[str, str] | None:
    if pane_id:
        pane_text = await tmux_manager.capture_pane_by_id(pane_id, window_id=window_id)
    else:
        w = await tmux_manager.find_window_by_id(window_id)
        if not w:
            return None
        pane_text = await tmux_manager.capture_pane(w.window_id)

    if not pane_text:
        logger.debug(
            "No pane text captured for window_id %s pane_id %s", window_id, pane_id
        )
        return None

    provider = get_provider_for_window(
        window_id, provider_name=get_window_provider(window_id)
    )
    pane_title = ""
    if provider.capabilities.uses_pane_title and not pane_id:
        pane_title = await tmux_manager.get_pane_title(window_id)
    status = provider.parse_terminal_status(pane_text, pane_title=pane_title)
    if status is None or not status.is_interactive:
        return None

    if not status.ui_type:
        logger.warning(
            "Interactive status with no ui_type in window_id %s pane %s",
            window_id,
            pane_id,
        )
        return None

    return status.ui_type, status.raw_text


def _lookup_pane_name(window_id: str, pane_id: str) -> str | None:
    from ...window_state_ports.pane_state import get_pane_projection

    pane = get_pane_projection(window_id, pane_id)
    return pane.name if pane else None


async def _send_interactive_with_retry(
    client: TelegramClient,
    *,
    chat_id: int,
    text: str,
    keyboard: InlineKeyboardMarkup,
    thread_kwargs: dict[str, int],
    ikey: tuple[int, int],
    thread_id: int | None,
    window_id: str,
    now: float,
) -> Message | None:
    for attempt in range(_INTERACTIVE_SEND_RETRIES + 1):
        try:
            return await client.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                **thread_kwargs,  # type: ignore[arg-type]
            )
        except BadRequest as e:
            if is_thread_gone(e):
                logger.warning(
                    "Topic gone for interactive UI (chat=%s thread=%s window=%s), "
                    "backing off %ss — use /sync to recreate",
                    chat_id,
                    thread_id,
                    window_id,
                    int(_DEAD_TOPIC_RETRY_INTERVAL),
                )
                _send_cooldowns[ikey] = (
                    now + _DEAD_TOPIC_RETRY_INTERVAL - _SEND_RETRY_INTERVAL
                )
            else:
                logger.error("Failed to send interactive UI to %s: %s", chat_id, e)
            return None
        except (TimedOut, NetworkError) as e:
            if attempt < _INTERACTIVE_SEND_RETRIES:
                logger.debug("Interactive UI send transient error, retrying: %s", e)
                await asyncio.sleep(_INTERACTIVE_SEND_RETRY_BACKOFF_S)
                continue
            logger.error("Failed to send interactive UI to %s: %s", chat_id, e)
            return None
        except TelegramError as e:
            logger.error("Failed to send interactive UI to %s: %s", chat_id, e)
            return None
    return None


async def handle_interactive_ui(
    client: TelegramClient,
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
    pane_id: str | None = None,
) -> bool:
    captured = await _capture_interactive_content(window_id, pane_id=pane_id)
    if not captured:
        return False

    ui_name, text = captured
    pane_name = _lookup_pane_name(window_id, pane_id) if pane_id else None
    text = format_interactive_message(text, pane_id=pane_id, pane_name=pane_name)
    ikey = (user_id, thread_id or 0)
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)
    keyboard = _build_interactive_keyboard(window_id, ui_name=ui_name, pane_id=pane_id)

    existing_msg_id = _interactive_msgs.get(ikey)
    if existing_msg_id:
        return (
            await _edit_interactive_msg(
                client, chat_id, existing_msg_id, text, keyboard, ikey, window_id
            )
            or False
        )

    now = time.monotonic()
    last_attempt = _send_cooldowns.get(ikey, 0.0)
    if now - last_attempt < _SEND_RETRY_INTERVAL:
        return False

    thread_kwargs: dict[str, int] = {}
    if thread_id is not None:
        thread_kwargs["message_thread_id"] = thread_id

    logger.info(
        "Sending interactive UI to user %d for window_id %s", user_id, window_id
    )
    _send_cooldowns[ikey] = now
    await rate_limit_send(chat_id)
    sent = await _send_interactive_with_retry(
        client,
        chat_id=chat_id,
        text=text,
        keyboard=keyboard,
        thread_kwargs=thread_kwargs,
        ikey=ikey,
        thread_id=thread_id,
        window_id=window_id,
        now=now,
    )
    if sent:
        _interactive_msgs[ikey] = sent.message_id
        _interactive_mode[ikey] = window_id
        _send_cooldowns.pop(ikey, None)
    return sent is not None


async def clear_interactive_msg(
    user_id: int,
    client: TelegramClient | None = None,
    thread_id: int | None = None,
) -> None:
    ikey = (user_id, thread_id or 0)
    msg_id = _interactive_msgs.pop(ikey, None)
    _interactive_mode.pop(ikey, None)
    _send_cooldowns.pop(ikey, None)
    logger.debug(
        "Clear interactive msg: user=%d, thread=%s, msg_id=%s",
        user_id,
        thread_id,
        msg_id,
    )
    if client and msg_id:
        chat_id = thread_router.resolve_chat_id(user_id, thread_id)
        with contextlib.suppress(TelegramError):
            await client.delete_message(chat_id=chat_id, message_id=msg_id)
