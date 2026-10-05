from __future__ import annotations

from typing import TYPE_CHECKING
import asyncio
import structlog

from telegram import CallbackQuery, Update
from ...telegram_client import PTBTelegramClient
from ...multiplexer import multiplexer as tmux_manager
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
from ..callback_registry import register
from .interactive_ui import clear_interactive_msg, handle_interactive_ui

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

INTERACTIVE_KEY_MAP: dict[str, tuple[str, bool]] = {
    CB_ASK_UP: ("Up", True),
    CB_ASK_DOWN: ("Down", True),
    CB_ASK_LEFT: ("Left", True),
    CB_ASK_RIGHT: ("Right", True),
    CB_ASK_ESC: ("Escape", False),
    CB_ASK_ENTER: ("Enter", True),
    CB_ASK_SPACE: ("Space", True),
    CB_ASK_TAB: ("Tab", True),
}

INTERACTIVE_KEY_LABELS: dict[str, str] = {
    CB_ASK_ESC: "\u238b Esc",
    CB_ASK_ENTER: "\u23ce Enter",
    CB_ASK_SPACE: "\u2423 Space",
    CB_ASK_TAB: "\u21e5 Tab",
}

INTERACTIVE_PREFIXES: tuple[str, ...] = (
    *INTERACTIVE_KEY_MAP,
    CB_ASK_REFRESH,
)


def match_interactive_prefix(data: str) -> tuple[str, str, str | None] | None:
    from ..callback_data import CB_PANE_DELIMITER

    for prefix in INTERACTIVE_PREFIXES:
        if data.startswith(prefix):
            remainder = data[len(prefix) :]
            if CB_PANE_DELIMITER in remainder:
                window_id, pane_id = remainder.split(CB_PANE_DELIMITER, 1)
                return prefix, window_id, pane_id
            return prefix, remainder, None
    return None


async def handle_interactive_callback(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    matched = match_interactive_prefix(data)
    if not matched:
        return

    cb_prefix, window_id, pane_id = matched
    from ..callback_helpers import get_thread_id, user_owns_window

    if not user_owns_window(user_id, window_id):
        await query.answer("Not your session", show_alert=True)
        return

    thread_id = get_thread_id(update)
    client = PTBTelegramClient(context.bot)

    if cb_prefix == CB_ASK_REFRESH:
        await handle_interactive_ui(
            client, user_id, window_id, thread_id, pane_id=pane_id
        )
        await query.answer("\U0001f504")
    else:
        tmux_key, refresh_ui = INTERACTIVE_KEY_MAP[cb_prefix]
        if pane_id:
            sent = await tmux_manager.send_keys_to_pane(
                pane_id, tmux_key, enter=False, literal=False, window_id=window_id
            )
        else:
            w = await tmux_manager.find_window_by_id(window_id)
            sent = bool(w) and await tmux_manager.send_keys(
                w.window_id, tmux_key, enter=False, literal=False
            )
        if sent and refresh_ui:
            await asyncio.sleep(0.5)
            await handle_interactive_ui(
                client, user_id, window_id, thread_id, pane_id=pane_id
            )
        elif sent and not refresh_ui:
            await clear_interactive_msg(user_id, client, thread_id)
        await query.answer(INTERACTIVE_KEY_LABELS.get(cb_prefix, ""))


@register(*INTERACTIVE_PREFIXES)
async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    assert query is not None and query.data is not None and user is not None
    await handle_interactive_callback(query, user.id, query.data, update, context)
