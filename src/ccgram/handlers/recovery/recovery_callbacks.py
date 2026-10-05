from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from telegram import CallbackQuery, Update

from ..callback_data import (
    CB_RECOVERY_BACK,
    CB_RECOVERY_BROWSE,
    CB_RECOVERY_CANCEL,
    CB_RECOVERY_CONTINUE,
    CB_RECOVERY_FRESH,
    CB_RECOVERY_PICK,
    CB_RECOVERY_RESUME,
)
from ..callback_registry import register
from ..user_state import (
    PENDING_THREAD_ID,
    PENDING_THREAD_TEXT,
    RECOVERY_SESSIONS,
    RECOVERY_WINDOW_ID,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()


def _clear_recovery_state(user_data: dict | None) -> None:
    if user_data is None:
        return
    for key in (
        PENDING_THREAD_ID,
        PENDING_THREAD_TEXT,
        RECOVERY_WINDOW_ID,
        RECOVERY_SESSIONS,
    ):
        user_data.pop(key, None)


async def handle_recovery_callback(
    query: CallbackQuery,
    user_id: int,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    from .recovery_banner import (
        _handle_back,
        _handle_browse,
        _handle_cancel,
        _handle_continue,
        _handle_fresh,
        _handle_resume,
    )

    from .resume_picker import _handle_resume_pick

    if data.startswith(CB_RECOVERY_BROWSE):
        await _handle_browse(query, user_id, data, update, context)
        return
    if data.startswith(CB_RECOVERY_BACK):
        await _handle_back(query, data, update, context)
        return
    if data.startswith(CB_RECOVERY_FRESH):
        await _handle_fresh(query, user_id, data, update, context)
        return
    if data.startswith(CB_RECOVERY_CONTINUE):
        await _handle_continue(query, user_id, data, update, context)
        return
    if data.startswith(CB_RECOVERY_RESUME):
        await _handle_resume(query, user_id, data, update, context)
        return
    if data.startswith(CB_RECOVERY_PICK):
        await _handle_resume_pick(query, user_id, data, update, context)
        return
    if data == CB_RECOVERY_CANCEL:
        await _handle_cancel(query, update, context)
        return
    logger.warning("Unhandled recovery callback data: %r", data)
    await query.answer()


@register(
    CB_RECOVERY_BACK,
    CB_RECOVERY_BROWSE,
    CB_RECOVERY_FRESH,
    CB_RECOVERY_CONTINUE,
    CB_RECOVERY_RESUME,
    CB_RECOVERY_PICK,
    CB_RECOVERY_CANCEL,
)
async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    assert query is not None and query.data is not None and user is not None
    await handle_recovery_callback(query, user.id, query.data, update, context)
