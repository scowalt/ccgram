from __future__ import annotations

from typing import TYPE_CHECKING
import structlog

from telegram import CallbackQuery, Update
from ...multiplexer import multiplexer as tmux_manager
from ..callback_data import CB_HISTORY_NEXT, CB_HISTORY_PREV
from ..callback_registry import register
from ..messaging_pipeline.message_sender import safe_edit
from .history import send_history

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

_HISTORY_CB_PARTS_MIN = 4


async def handle_history_callback(
    query: CallbackQuery,
    _user_id: int,
    data: str,
    _update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
) -> None:
    prefix_len = len(CB_HISTORY_PREV)
    rest = data[prefix_len:]
    try:
        parts = rest.split(":")
        if len(parts) < _HISTORY_CB_PARTS_MIN:
            offset_str, window_id = rest.split(":", 1)
            start_byte, end_byte = 0, 0
        else:
            offset_str = parts[0]
            start_byte = int(parts[-2])
            end_byte = int(parts[-1])
            window_id = ":".join(parts[1:-2])
        offset = int(offset_str)
    except ValueError, IndexError:
        await query.answer("Invalid data")
        return

    w = await tmux_manager.find_window_by_id(window_id)
    if w:
        await send_history(
            query,
            window_id,
            offset=offset,
            edit=True,
            start_byte=start_byte,
            end_byte=end_byte,
        )
    else:
        await safe_edit(query, "Window no longer exists.")
    await query.answer("Page updated")


@register(CB_HISTORY_PREV, CB_HISTORY_NEXT)
async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    assert query is not None and query.data is not None and user is not None
    await handle_history_callback(query, user.id, query.data, update, context)
