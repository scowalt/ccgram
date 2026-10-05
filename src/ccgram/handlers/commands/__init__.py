from __future__ import annotations


from typing import TYPE_CHECKING

import structlog
from telegram import Update

from ...cc_commands import discover_provider_commands
from ...config import config
from ...providers import get_provider_for_window
from ... import window_query
from ...thread_router import thread_router
from ...utils import handle_general_topic_message, is_general_topic
from ..callback_helpers import get_thread_id as _get_thread_id
from ..messaging_pipeline.message_sender import safe_reply
from ..toolbar import build_toolbar_keyboard, seed_button_states
from .forward import forward_command_handler
from .menu_sync import (
    get_global_provider_menu,
    set_global_provider_menu,
    setup_menu_refresh_job,
    sync_scoped_menu_for_text_context,
    sync_scoped_provider_menu,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()


async def commands_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:

    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    window_id = thread_router.resolve_window_for_thread(user.id, thread_id)
    if not window_id:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    provider = get_provider_for_window(
        window_id, provider_name=window_query.get_window_provider(window_id)
    )
    await sync_scoped_provider_menu(update.message, user.id, provider)
    commands = discover_provider_commands(provider)
    if not commands:
        await safe_reply(
            update.message,
            f"Provider: `{provider.capabilities.name}`\nNo discoverable commands.",
        )
        return

    lines = [f"Provider: `{provider.capabilities.name}`", "Supported commands:"]
    for cmd in sorted(commands, key=lambda c: c.telegram_name):
        if not cmd.telegram_name:
            continue
        original = cmd.name if cmd.name.startswith("/") else f"/{cmd.name}"
        lines.append(f"- `/{cmd.telegram_name}` → `{original}`")
    await safe_reply(update.message, "\n".join(lines))


async def toolbar_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:

    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    if thread_id is None:
        if (
            update.message
            and update.effective_chat
            and is_general_topic(update.message)
        ):
            await handle_general_topic_message(
                update.get_bot(), update.message, update.effective_chat.id
            )
        else:
            await safe_reply(update.message, "❌ Use this command inside a topic.")
        return

    window_id = thread_router.get_window_for_thread(user.id, thread_id)
    if not window_id:
        await safe_reply(update.message, "❌ This topic is not bound to any session.")
        return

    provider_name = window_query.get_window_provider(window_id) or "claude"
    await seed_button_states(window_id)
    keyboard = build_toolbar_keyboard(window_id, provider_name)
    display = thread_router.get_display_name(window_id)
    await safe_reply(
        update.message,
        f"\U0001f39b `{display}` toolbar",
        reply_markup=keyboard,
    )


__all__ = [
    "commands_command",
    "forward_command_handler",
    "get_global_provider_menu",
    "set_global_provider_menu",
    "setup_menu_refresh_job",
    "sync_scoped_menu_for_text_context",
    "sync_scoped_provider_menu",
    "toolbar_command",
]
