"""/restore command — recover dead topics via explicit command.

When a tmux window dies, the topic becomes stale. This command gives users
an explicit way to trigger recovery, showing the same recovery keyboard
that the text handler shows for dead windows.

Key function: restore_command().
"""

from pathlib import Path

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from ..config import config
from ..session import session_manager
from ..tmux_manager import tmux_manager
from .message_sender import safe_reply
from .recovery_callbacks import build_recovery_keyboard

logger = structlog.get_logger()


async def restore_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /restore — show recovery UI for a dead topic."""
    user = update.effective_user
    if not user or not update.message:
        return

    if not config.is_user_allowed(user.id):
        await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    thread_id = update.message.message_thread_id
    if thread_id is None:
        await safe_reply(update.message, "Use this command inside a topic.")
        return

    window_id = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not window_id:
        await safe_reply(update.message, "No session bound to this topic.")
        return

    window = await tmux_manager.find_window_by_id(window_id)
    if window is not None:
        await safe_reply(
            update.message, "Window is still running — nothing to restore."
        )
        return

    ws = session_manager.get_window_state(window_id)
    cwd = ws.cwd or ""
    if not cwd or not Path(cwd).is_dir():
        await safe_reply(update.message, "Directory no longer exists.")
        return

    display = session_manager.get_display_name(window_id)
    keyboard = build_recovery_keyboard(window_id)
    await safe_reply(
        update.message,
        f"\u26a0 Window `{display}` is no longer running.\n"
        f"\U0001f4c2 `{cwd}`\n\n"
        "How would you like to recover?",
        reply_markup=keyboard,
    )
