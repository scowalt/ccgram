from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ...multiplexer import multiplexer as tmux_manager
from ..callback_data import CB_WS_SELECT, CB_WS_SKIP

from ..messaging_pipeline.message_sender import safe_edit
from .directory_browser import (
    build_provider_picker,
    build_workspace_picker,
    clear_workspace_state,
)
from .topic_creation_draft import (
    PENDING_WORKSPACE_ID,
    PENDING_WORKSPACES,
    _browser_flow_stale,
    _required_selected_path,
)

if TYPE_CHECKING:
    from telegram import CallbackQuery, Update
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

__all__ = [
    "_show_provider_picker",
    "_show_workspace_picker_or_provider",
    "_handle_workspace_callback",
]


async def _show_provider_picker(query: CallbackQuery, selected_path: str) -> None:
    text, keyboard = build_provider_picker(selected_path)
    await safe_edit(query, text, reply_markup=keyboard)


async def _show_workspace_picker_or_provider(
    query: CallbackQuery,
    selected_path: str,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> None:
    if tmux_manager.capabilities.native_agent_status:
        workspaces = await tmux_manager.list_workspaces()
        ws_triples = [(w.workspace_id, w.label, w.cwd) for w in workspaces]
        if context is not None and context.user_data is not None:
            context.user_data[PENDING_WORKSPACES] = ws_triples
        if ws_triples:
            text, keyboard = build_workspace_picker(selected_path, ws_triples)
            await safe_edit(query, text, reply_markup=keyboard)
            return
        if context is not None:
            clear_workspace_state(context.user_data)
    await _show_provider_picker(query, selected_path)


async def _handle_workspace_callback(
    query: CallbackQuery,
    data: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if _browser_flow_stale(update, context):
        await query.answer("Stale browser (flow reset)", show_alert=True)
        return

    selected_path = _required_selected_path(context)
    if selected_path is None:
        await query.answer("Stale browser (flow reset)", show_alert=True)
        return

    await query.answer()

    if data == CB_WS_SKIP:
        clear_workspace_state(context.user_data)
        await _show_provider_picker(query, selected_path)
        return

    try:
        idx = int(data[len(CB_WS_SELECT) :])
    except ValueError, IndexError:
        await safe_edit(query, "❌ Invalid workspace selection. Tap Cancel and retry.")
        return

    workspaces: list[tuple[str, str, str]] = (
        context.user_data.get(PENDING_WORKSPACES, []) if context.user_data else []
    )
    if idx < 0 or idx >= len(workspaces):
        await safe_edit(query, "❌ Workspace list changed. Tap Cancel and retry.")
        return

    chosen_ws_id = workspaces[idx][0]
    if context.user_data is not None:
        context.user_data[PENDING_WORKSPACE_ID] = chosen_ws_id
        context.user_data.pop(PENDING_WORKSPACES, None)
    await _show_provider_picker(query, selected_path)
