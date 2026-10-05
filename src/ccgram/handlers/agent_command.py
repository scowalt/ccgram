from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)

from ..config import config
from ..session import session_manager
from ..telegram_client import PTBTelegramClient, TelegramClient
from ..thread_router import thread_router
from ..window_state_ports import identity_state
from .callback_data import CB_AGENT_CANCEL, CB_AGENT_SET
from .callback_helpers import get_thread_id, user_owns_window
from .callback_registry import register
from .messaging_pipeline.message_sender import safe_edit, safe_reply

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

_BUTTONS_PER_ROW = 3

_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("claude", "Claude"),
    ("codex", "Codex"),
    ("gemini", "Gemini"),
    ("pi", "Pi"),
    ("shell", "Shell"),
)
_VALID_NAMES = frozenset(name for name, _ in _PROVIDERS) | {"auto"}


def _resolve_window(update: Update) -> tuple[int, int, str] | None:
    user = update.effective_user
    if not user or not config.is_user_allowed(user.id):
        return None
    thread_id = get_thread_id(update)
    if thread_id is None:
        return None
    window_id = thread_router.get_window_for_thread(user.id, thread_id)
    if not window_id:
        return None
    return user.id, thread_id, window_id


def _build_keyboard(window_id: str, current: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for name, label in _PROVIDERS:
        prefix = "✓ " if name == current else ""
        row.append(
            InlineKeyboardButton(
                f"{prefix}{label}", callback_data=f"{CB_AGENT_SET}{window_id}:{name}"
            )
        )
        if len(row) == _BUTTONS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                "🔄 Auto", callback_data=f"{CB_AGENT_SET}{window_id}:auto"
            ),
            InlineKeyboardButton(
                "Cancel", callback_data=f"{CB_AGENT_CANCEL}{window_id}"
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _picker_text(window_id: str) -> str:
    current = identity_state.get_provider_name(window_id) or "(unknown)"
    override = identity_state.is_provider_manually_overridden(window_id)
    badge = " (manual override)" if override else ""
    return (
        f"Current agent for `{window_id}`: **{current}**{badge}\n\n"
        "Pick a provider, or **Auto** to re-detect."
    )


async def _apply_switch(
    window_id: str,
    chosen: str,
    *,
    client: TelegramClient | None = None,
    chat_id: int = 0,
    thread_id: int = 0,
) -> tuple[str, str]:
    current = identity_state.get_provider_name(window_id) or ""
    if chosen == "auto":
        target = await _redetect_provider(window_id)
        manual = False
        reply_intro = f"Auto-detected: **{target}**."
    else:
        target = chosen
        manual = True
        reply_intro = (
            f"Agent set to **{target}** (manual override)."
            if target != "shell"
            else "Agent set to **shell**."
        )

    _commit_switch(window_id, target, current, manual=manual)

    if target == "shell":
        from .shell.shell_prompt_orchestrator import ensure_setup

        await ensure_setup(
            window_id,
            "provider_switch",
            client=client,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        reply = f"{reply_intro} Prompt markers will install on next prompt."
    elif chosen == "auto":
        reply = reply_intro
    else:
        reply = (
            f"{reply_intro}\n"
            "Launch the agent CLI in this pane; next SessionStart hook will track it."
        )
    return target, reply


async def _redetect_provider(window_id: str) -> str:
    from ..providers import detect_provider_from_pane

    from ..multiplexer import multiplexer as tmux_manager

    w = await tmux_manager.find_window_by_id(window_id)
    detected = ""
    if w and w.pane_current_command:
        detected = await detect_provider_from_pane(
            w.pane_current_command, window_id=window_id
        )
    return detected or "shell"


def _commit_switch(window_id: str, chosen: str, current: str, *, manual: bool) -> None:
    same_provider = current == chosen
    session_manager.set_window_provider(window_id, chosen)
    if not same_provider:
        identity_state.clear_transcript_path(window_id)
    identity_state.set_provider_manual_override(window_id, value=manual)
    if same_provider:
        return
    if chosen != "shell":
        return
    from .shell.shell_capture import clear_shell_monitor_state

    from .shell.shell_prompt_orchestrator import clear_state as clear_orchestrator

    clear_shell_monitor_state(window_id)
    clear_orchestrator(window_id)


async def agent_command(update: Update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
    resolved = _resolve_window(update)
    if resolved is None or not update.message:
        if update.message:
            await safe_reply(update.message, "Use /agent inside a bound topic.")
        return
    _user_id, thread_id, window_id = resolved

    args = (update.message.text or "").split(maxsplit=1)
    arg = args[1].strip().lower() if len(args) > 1 else ""
    if not arg:
        await safe_reply(
            update.message,
            _picker_text(window_id),
            reply_markup=_build_keyboard(
                window_id, identity_state.get_provider_name(window_id) or ""
            ),
        )
        return
    if arg not in _VALID_NAMES:
        await safe_reply(
            update.message,
            f"Unknown agent `{arg}`. Use one of: {', '.join(sorted(_VALID_NAMES))}.",
        )
        return
    client = PTBTelegramClient(update.get_bot())
    chat_id = update.message.chat.id
    _, reply = await _apply_switch(
        window_id, arg, client=client, chat_id=chat_id, thread_id=thread_id
    )
    await safe_reply(update.message, reply)


@register(CB_AGENT_SET, CB_AGENT_CANCEL)
async def _dispatch(update: Update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if query.data.startswith(CB_AGENT_CANCEL):
        window_id = query.data[len(CB_AGENT_CANCEL) :]
        user = update.effective_user
        if user is None or not user_owns_window(user.id, window_id):
            await query.answer("Not your window")
            return
        await _ack_and_strip(
            query,
            f"Cancelled. Agent still **{identity_state.get_provider_name(window_id) or '(unknown)'}**.",
        )
        return
    payload = query.data[len(CB_AGENT_SET) :]
    if ":" not in payload:
        await query.answer("Bad callback")
        return
    window_id, chosen = payload.rsplit(":", 1)
    user = update.effective_user
    if user is None or not user_owns_window(user.id, window_id):
        await query.answer("Not your window")
        return
    if chosen not in _VALID_NAMES:
        await query.answer("Unknown provider")
        return
    client = PTBTelegramClient(query.get_bot())
    chat_id = query.message.chat.id if query.message else 0
    thread_id = get_thread_id(update) or 0
    _, reply = await _apply_switch(
        window_id, chosen, client=client, chat_id=chat_id, thread_id=thread_id
    )
    await _ack_and_strip(query, reply)


async def _ack_and_strip(query: CallbackQuery, text: str) -> None:
    await query.answer()
    await safe_edit(query, text, reply_markup=None)
