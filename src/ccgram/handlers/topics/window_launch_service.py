from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from telegram.error import TelegramError

from ...providers import registry as provider_registry
from ...session import session_manager
from ...session_map import session_map_sync
from ...thread_router import thread_router
from ...multiplexer import multiplexer as tmux_manager
from ...multiplexer.window_ops import send_to_window
from ...user_preferences import user_preferences
from ...window_state_store import CCGRAM_CREATED_WINDOW_ORIGIN
from ..messaging_pipeline.message_sender import safe_edit, safe_send
from ..status.topic_emoji import format_topic_name_for_mode
from .directory_browser import clear_worktree_state, clear_workspace_state
from .topic_creation_draft import (
    PENDING_THREAD_ID,
    PENDING_THREAD_TEXT,
    PENDING_WORKSPACE_ID,
    PENDING_WORKTREE_BRANCH,
    PENDING_WORKTREE_PATH,
    PENDING_WORKTREE_REPO,
)
from . import topic_orchestration

if TYPE_CHECKING:
    from telegram import CallbackQuery
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

__all__ = [
    "WindowLaunchRequest",
    "WindowLaunchResult",
    "launch_window",
]


@dataclass
class WindowLaunchRequest:
    user_id: int
    thread_id: int | None
    provider_name: str
    cwd: str
    mode: str
    pending_text: str | None


@dataclass
class WindowLaunchResult:
    success: bool
    window_id: str | None = None
    error_message: str | None = None


def _cwd_within(cwd: str, worktree_path: str) -> bool:
    try:
        c = Path(cwd).resolve()
        w = Path(worktree_path).resolve()
    except OSError:
        return False
    return c == w or c.is_relative_to(w)


def _persist_worktree_state(
    window_id: str, cwd: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user_data = context.user_data
    worktree_path = user_data.get(PENDING_WORKTREE_PATH) if user_data else None
    worktree_branch = user_data.get(PENDING_WORKTREE_BRANCH) if user_data else None
    if worktree_path and worktree_branch and _cwd_within(cwd, worktree_path):
        session_manager.set_window_worktree(window_id, worktree_path, worktree_branch)
    clear_worktree_state(user_data)


async def _abort_topic_creation(
    query: CallbackQuery, message: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await safe_edit(query, f"❌ {message}")
    if context.user_data is not None:
        context.user_data.pop(PENDING_THREAD_ID, None)
        context.user_data.pop(PENDING_THREAD_TEXT, None)
    clear_worktree_state(context.user_data)
    clear_workspace_state(context.user_data)


async def _create_topic_window(
    selected_path: str,
    launch_command: str | None,
    chosen_workspace_id: str | None,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[bool, str, str, str]:
    ud = context.user_data
    wt_repo = ud.get(PENDING_WORKTREE_REPO) if ud else None
    wt_branch = ud.get(PENDING_WORKTREE_BRANCH) if ud else None
    wt_path = ud.get(PENDING_WORKTREE_PATH) if ud else None
    if tmux_manager.capabilities.native_worktrees and wt_repo and wt_branch and wt_path:
        return await tmux_manager.create_worktree_window(
            wt_repo,
            wt_path,
            wt_branch,
            window_name=Path(wt_path).name,
            launch_command=launch_command,
        )
    return await tmux_manager.create_window(
        selected_path,
        launch_command=launch_command,
        workspace_id=chosen_workspace_id,
    )


async def _wait_for_shell_ready(window_id: str, *, attempts: int = 5) -> None:
    import os

    from ccgram.providers.shell import KNOWN_SHELLS

    for _ in range(attempts):
        w = await tmux_manager.find_window_by_id(window_id)
        if w and w.pane_current_command:
            cmd = os.path.basename(w.pane_current_command.split()[0]).lstrip("-")
            if cmd in KNOWN_SHELLS:
                return
        await asyncio.sleep(0.2)


async def _accept_yolo_confirmation(window_id: str, *, timeout: float = 8.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        text = await tmux_manager.capture_pane(window_id)
        if text and "bypass permissions" in text.lower():
            await asyncio.sleep(0.3)
            await tmux_manager.send_keys(window_id, "Down", enter=False, literal=False)
            await asyncio.sleep(0.15)
            await tmux_manager.send_keys(window_id, "Enter", enter=False, literal=False)
            logger.info("Accepted bypass permissions prompt for window %s", window_id)
            return True
        await asyncio.sleep(0.5)
    logger.warning(
        "Bypass permissions prompt not detected within %.0fs for window %s",
        timeout,
        window_id,
    )
    return False


async def launch_window(  # noqa: PLR0915, C901
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    request: WindowLaunchRequest,
) -> WindowLaunchResult:
    from ccgram.providers import resolve_launch_command

    user_id = request.user_id
    pending_thread_id = request.thread_id
    selected_path = request.cwd
    provider_name = request.provider_name
    approval_mode = request.mode

    launch_command = resolve_launch_command(provider_name, approval_mode=approval_mode)

    chosen_workspace_id: str | None = (
        context.user_data.get(PENDING_WORKSPACE_ID) if context.user_data else None
    ) or None

    success, message, created_wname, created_wid = await _create_topic_window(
        selected_path, launch_command, chosen_workspace_id, context
    )
    if not success:
        await _abort_topic_creation(query, message, context)
        return WindowLaunchResult(success=False, error_message=message)
    clear_workspace_state(context.user_data)

    topic_orchestration.register_pending_creation(created_wid)

    user_preferences.update_user_mru(user_id, selected_path)
    session_manager.set_window_origin(created_wid, CCGRAM_CREATED_WINDOW_ORIGIN)
    session_manager.set_window_cwd(created_wid, selected_path)
    session_manager.set_window_provider(created_wid, provider_name)
    session_manager.set_window_approval_mode(created_wid, approval_mode)
    _persist_worktree_state(created_wid, selected_path, context)
    logger.info(
        "Window created: %s (id=%s) at %s provider=%s mode=%s (user=%d, thread=%s)",
        created_wname,
        created_wid,
        selected_path,
        provider_name,
        approval_mode,
        user_id,
        pending_thread_id,
    )
    await tmux_manager.stamp_pane_title(created_wid, provider_name)

    provider_caps = provider_registry.get(provider_name).capabilities
    if provider_caps.chat_first_command_path:
        from ..shell.shell_prompt_orchestrator import ensure_setup

        await _wait_for_shell_ready(created_wid)
        await ensure_setup(created_wid, "auto")

    if pending_thread_id is not None:
        thread_router.bind_thread(
            user_id, pending_thread_id, created_wid, window_name=created_wname
        )
        query_message = query.message
        chat = query_message.chat if query_message else None
        if chat and chat.type in ("group", "supergroup"):
            thread_router.set_group_chat_id(user_id, pending_thread_id, chat.id)
        topic_orchestration.clear_pending_creation(created_wid)

    provider = provider_registry.get(provider_name)
    if approval_mode == "yolo" and provider.capabilities.has_yolo_confirmation:
        await _accept_yolo_confirmation(created_wid)

    if provider.capabilities.supports_hook:
        await session_map_sync.wait_for_session_map_entry(created_wid)

    if pending_thread_id is None:
        await safe_edit(query, f"✅ {message}")
        return WindowLaunchResult(success=True, window_id=created_wid)

    try:
        await context.bot.edit_forum_topic(
            chat_id=thread_router.resolve_chat_id(user_id, pending_thread_id),
            message_thread_id=pending_thread_id,
            name=format_topic_name_for_mode(created_wname, approval_mode),
        )
    except TelegramError as e:
        logger.debug("Failed to rename topic: %s", e)

    await safe_edit(
        query,
        f"✅ {message}\n\nBound to this topic. Send messages here.",
    )

    pending_text = request.pending_text
    if pending_text:
        logger.debug(
            "Forwarding pending text to window %s (len=%d)",
            created_wname,
            len(pending_text),
        )
        if context.user_data is not None:
            context.user_data.pop(PENDING_THREAD_TEXT, None)
            context.user_data.pop(PENDING_THREAD_ID, None)

        if provider_caps.chat_first_command_path:
            from ...telegram_client import PTBTelegramClient

            from ..shell.shell_commands import handle_shell_message

            await handle_shell_message(
                PTBTelegramClient(context.bot),
                user_id,
                pending_thread_id,
                created_wid,
                pending_text,
            )
        else:
            send_ok, send_msg = await send_to_window(created_wid, pending_text)
            if not send_ok:
                logger.warning(
                    "Failed to forward pending text to window %s (user %s): %s",
                    created_wid,
                    user_id,
                    send_msg,
                )
                from ...telegram_client import PTBTelegramClient

                await safe_send(
                    PTBTelegramClient(context.bot),
                    thread_router.resolve_chat_id(user_id, pending_thread_id),
                    f"❌ Failed to send pending message: {send_msg}",
                    message_thread_id=pending_thread_id,
                )
    elif context.user_data is not None:
        context.user_data.pop(PENDING_THREAD_ID, None)
    return WindowLaunchResult(success=True, window_id=created_wid)
