from __future__ import annotations

from typing import TYPE_CHECKING
import asyncio
import contextlib
import re

import structlog
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest, TelegramError
from .. import window_query
from ..config import config
from ..session import AuditIssue, AuditResult, session_manager
from ..session_map import session_map_sync
from ..telegram_client import PTBTelegramClient, TelegramClient
from ..thread_router import thread_router
from ..multiplexer import multiplexer as tmux_manager
from ..multiplexer.reconciliation import list_windows_for_reconciliation
from ..user_preferences import user_preferences
from .callback_data import CB_SYNC_DISMISS, CB_SYNC_FIX
from .callback_registry import register
from .cleanup import clear_topic_state
from .messaging_pipeline.message_sender import is_thread_gone, safe_edit, safe_reply
from .status.topic_emoji import sync_topic_name

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

_GHOST_RE = re.compile(r"user:(\d+)\s+thread:(\d+)\s+window:([^\s(]+)")
_WINDOW_RE = re.compile(r"([^\s(]+)")

_CATEGORY_LABELS: dict[str, str] = {
    "ghost_binding": "ghost binding (dead window)",
    "dead_topic": "dead topic (window alive, topic deleted)",
    "orphaned_display_name": "orphaned display name",
    "orphaned_group_chat_id": "orphaned group chat ID",
    "stale_window_state": "stale window state",
    "stale_offset": "stale offset entry",
    "display_name_drift": "display name drift",
    "orphaned_window": "unbound window (no topic)",
}


async def _run_audit() -> AuditResult:
    all_windows = await tmux_manager.list_windows()
    live_ids = {w.window_id for w in all_windows}
    live_pairs = [(w.window_id, w.window_name) for w in all_windows]
    return session_manager.audit_state(live_ids, live_pairs)


def _issue_summary_lines(audit: AuditResult) -> list[str]:
    category_counts: dict[str, int] = {}
    for issue in audit.issues:
        if issue.category in ("ghost_binding", "dead_topic"):
            continue
        category_counts[issue.category] = category_counts.get(issue.category, 0) + 1

    if category_counts:
        return [
            f"⚠ {count} {_CATEGORY_LABELS.get(cat, cat)}"
            for cat, count in category_counts.items()
        ]
    if audit.total_bindings > 0:
        return ["✓ No orphaned entries", "✓ Tmux display cache in sync"]
    return []


async def _sync_live_topic_names(
    client: TelegramClient, live_ids: set[str] | None = None
) -> None:
    if live_ids is None:
        all_windows = await tmux_manager.list_windows()
        live_ids = {w.window_id for w in all_windows}

    for user_id, thread_id, window_id in thread_router.iter_thread_bindings():
        if window_id not in live_ids:
            continue
        chat_id = thread_router.resolve_chat_id(user_id, thread_id)
        if chat_id == user_id:
            continue
        await sync_topic_name(
            client,
            chat_id,
            thread_id,
            thread_router.get_display_name(window_id),
        )


def _format_report(
    audit: AuditResult,
    *,
    fixed_count: int = 0,
    closed_topic_count: int = 0,
    recreated_topic_count: int = 0,
    manual_close_count: int = 0,
) -> tuple[str, InlineKeyboardMarkup | None]:
    lines: list[str] = []

    if fixed_count > 0:
        issue_word = "issue" if fixed_count == 1 else "issues"
        lines.append(f"✅ Fixed {fixed_count} {issue_word}\n")
    else:
        lines.append("🔍 State audit\n")

    if closed_topic_count > 0:
        topic_word = "topic" if closed_topic_count == 1 else "topics"
        lines.append(f"ℹ Removed {closed_topic_count} stale {topic_word}")

    if recreated_topic_count > 0:
        topic_word = "topic" if recreated_topic_count == 1 else "topics"
        lines.append(f"ℹ Recreated {recreated_topic_count} {topic_word}")

    if manual_close_count > 0:
        topic_word = "topic" if manual_close_count == 1 else "topics"
        lines.append(
            f"⚠ {manual_close_count} {topic_word} could not be closed automatically; "
            "safe to close manually"
        )

    if audit.total_bindings == 0:
        lines.append("ℹ No topic bindings")
    elif audit.live_binding_count == audit.total_bindings:
        lines.append(f"✓ {audit.total_bindings} topics bound, all windows alive")
    else:
        dead = audit.total_bindings - audit.live_binding_count
        lines.append(
            f"⚠ {dead} ghost binding(s) "
            f"({audit.live_binding_count}/{audit.total_bindings} alive)"
        )

    dead_topic_count = sum(1 for i in audit.issues if i.category == "dead_topic")
    if dead_topic_count > 0:
        topic_word = "topic" if dead_topic_count == 1 else "topics"
        lines.append(f"⚠ {dead_topic_count} dead {topic_word} (deleted in Telegram)")

    lines.extend(_issue_summary_lines(audit))

    text = "\n".join(lines)

    fixable = audit.fixable_count
    if fixable > 0:
        issue_word = "issue" if fixable == 1 else "issues"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"\U0001f527 Fix {fixable} {issue_word}",
                        callback_data=CB_SYNC_FIX,
                    ),
                    InlineKeyboardButton("✕ Dismiss", callback_data=CB_SYNC_DISMISS),
                ]
            ]
        )
    else:
        keyboard = None

    return text, keyboard


async def _remove_topic(client: TelegramClient, chat_id: int, thread_id: int) -> bool:
    try:
        await client.delete_forum_topic(chat_id, thread_id)
        return True
    except BadRequest as e:
        if is_thread_gone(e):
            return True
    except TelegramError:
        pass
    try:
        await client.close_forum_topic(chat_id, thread_id)
        return True
    except TelegramError:
        return False


async def _close_ghost_topics(
    client: TelegramClient, issues: list[AuditIssue]
) -> tuple[int, int]:
    closed_count = 0
    manual_close_count = 0
    for issue in issues:
        if issue.category != "ghost_binding":
            continue
        match = _GHOST_RE.search(issue.detail)
        if not match:
            continue
        user_id = int(match.group(1))
        thread_id = int(match.group(2))
        window_id = match.group(3)
        current_window_id = thread_router.get_window_for_thread(user_id, thread_id)
        if current_window_id != window_id:
            continue
        chat_id = thread_router.resolve_chat_id(user_id, thread_id)
        topic_removed = False
        if chat_id == user_id:
            logger.warning(
                "No group chat_id for ghost topic thread=%d, skipping close",
                thread_id,
            )
        else:
            topic_removed = await _remove_topic(client, chat_id, thread_id)
            if not topic_removed:
                logger.warning(
                    "Failed to delete/close ghost topic thread=%d window=%s",
                    thread_id,
                    window_id,
                )
                manual_close_count += 1
                continue
        if topic_removed or chat_id == user_id:
            try:
                await clear_topic_state(
                    user_id, thread_id, client=client, window_id=window_id
                )
                thread_router.unbind_thread(user_id, thread_id)
                if topic_removed:
                    closed_count += 1
            except OSError, TelegramError:
                logger.exception(
                    "Failed to clean up ghost binding thread=%d window=%s",
                    thread_id,
                    window_id,
                )
    return closed_count, manual_close_count


async def _adopt_orphaned_windows(
    client: TelegramClient, issues: list[AuditIssue]
) -> None:
    from ..session_monitor import NewWindowEvent

    from .topics.topic_orchestration import handle_new_window as _handle_new_window

    for issue in issues:
        if issue.category != "orphaned_window":
            continue
        match = _WINDOW_RE.search(issue.detail)
        if not match:
            continue
        window_id = match.group(1)
        view = window_query.view_window(window_id)
        name = (view.window_name if view else "") or thread_router.get_display_name(
            window_id
        )
        event = NewWindowEvent(
            window_id=window_id,
            session_id=view.session_id if view else "",
            window_name=name,
            cwd=view.cwd if view else "",
        )
        try:
            await _handle_new_window(event, client)
        except TelegramError:
            logger.exception("Failed to adopt orphaned window %s", window_id)


async def _probe_dead_topics(client: TelegramClient) -> list[AuditIssue]:
    bindings = [
        (uid, tid, wid, thread_router.resolve_chat_id(uid, tid))
        for uid, tid, wid in thread_router.iter_thread_bindings()
    ]
    bindings = [(uid, tid, wid, cid) for uid, tid, wid, cid in bindings if cid != uid]
    if not bindings:
        return []

    sem = asyncio.Semaphore(5)

    async def _probe_one(
        user_id: int, thread_id: int, window_id: str, chat_id: int
    ) -> AuditIssue | None:
        async with sem:
            try:
                msg = await client.send_message(
                    chat_id,
                    "​",
                    message_thread_id=thread_id,
                    disable_notification=True,
                )
                with contextlib.suppress(TelegramError):
                    await client.delete_message(chat_id, msg.message_id)
            except BadRequest as exc:
                if is_thread_gone(exc):
                    display = thread_router.get_display_name(window_id)
                    return AuditIssue(
                        category="dead_topic",
                        detail=f"user:{user_id} thread:{thread_id} window:{window_id} ({display})",
                        fixable=True,
                    )
            except TelegramError:
                pass
        return None

    results = await asyncio.gather(
        *(_probe_one(*b) for b in bindings), return_exceptions=True
    )
    issues: list[AuditIssue] = []
    for r in results:
        if isinstance(r, AuditIssue):
            issues.append(r)
        elif isinstance(r, BaseException):
            logger.error("Unexpected error probing dead topics", exc_info=r)
    return issues


def _parse_dead_topic_issue(issue: AuditIssue) -> tuple[int, int, str] | None:
    if issue.category != "dead_topic":
        return None
    match = _GHOST_RE.search(issue.detail)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), match.group(3)


def _restore_dead_topic_binding(
    user_id: int, thread_id: int, window_id: str, name: str, chat_id: int
) -> None:
    thread_router.bind_thread(user_id, thread_id, window_id, window_name=name)
    if chat_id != user_id:
        thread_router.set_group_chat_id(user_id, thread_id, chat_id)


async def _recreate_dead_topics(
    client: TelegramClient, issues: list[AuditIssue]
) -> int:
    from ..session_monitor import NewWindowEvent

    from .topics.topic_orchestration import handle_new_window as _handle_new_window

    recreated = 0
    for issue in issues:
        parsed = _parse_dead_topic_issue(issue)
        if parsed is None:
            continue
        user_id, thread_id, window_id = parsed
        current_window_id = thread_router.get_window_for_thread(user_id, thread_id)
        if current_window_id != window_id:
            continue

        view = window_query.view_window(window_id)
        name = (view.window_name if view else "") or thread_router.get_display_name(
            window_id
        )
        event = NewWindowEvent(
            window_id=window_id,
            session_id=view.session_id if view else "",
            window_name=name,
            cwd=view.cwd if view else "",
        )

        chat_id = thread_router.resolve_chat_id(user_id, thread_id)

        thread_router.unbind_thread(user_id, thread_id)

        _placeholder_key = f"{user_id}:0"
        if chat_id != user_id:
            thread_router.group_chat_ids[_placeholder_key] = chat_id

        try:
            await _handle_new_window(event, client)
        except TelegramError, OSError:
            logger.exception("Failed to recreate topic for window %s", window_id)
            if not thread_router.has_window(window_id):
                _restore_dead_topic_binding(
                    user_id, thread_id, window_id, name, chat_id
                )
        else:
            if thread_router.has_window(window_id):
                recreated += 1
            else:
                logger.warning(
                    "Topic recreation for window %s did not produce a binding; "
                    "restoring thread %d",
                    window_id,
                    thread_id,
                )
                _restore_dead_topic_binding(
                    user_id, thread_id, window_id, name, chat_id
                )
        finally:
            thread_router.group_chat_ids.pop(_placeholder_key, None)
    return recreated


async def sync_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return

    if not config.is_user_allowed(user.id):
        await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    status_msg = await safe_reply(update.message, "🔍 State audit…")
    client = PTBTelegramClient(update.get_bot())
    await _sync_live_topic_names(client)
    audit = await _run_audit()
    dead_issues = await _probe_dead_topics(client)
    audit.issues.extend(dead_issues)
    text, keyboard = _format_report(audit)
    if status_msg is not None:
        await safe_edit(status_msg, text, reply_markup=keyboard)
    else:
        await safe_reply(update.message, text, reply_markup=keyboard)


async def handle_sync_fix(query: CallbackQuery) -> None:
    await safe_edit(query, "🔧 Fixing…", reply_markup=None)

    all_windows = await list_windows_for_reconciliation(tmux_manager)
    if all_windows is None:
        await safe_edit(
            query,
            "⚠ Multiplexer unavailable. No state changes were made.",
            reply_markup=None,
        )
        return

    live_ids = {w.window_id for w in all_windows}
    live_pairs = [(w.window_id, w.window_name) for w in all_windows]

    client = PTBTelegramClient(query.get_bot())
    pre_audit = session_manager.audit_state(live_ids, live_pairs)
    dead_issues = await _probe_dead_topics(client)
    pre_audit.issues.extend(dead_issues)

    try:
        session_manager.sync_display_names(live_pairs)
        session_manager.prune_stale_state(live_ids)
        session_map_sync.prune_session_map(live_ids)
        session_manager.prune_stale_window_states(live_ids)
        bound_ids: set[str] = {
            wid for _, _, wid in thread_router.iter_thread_bindings()
        }
        state_ids = set(window_query.iter_window_ids())
        user_preferences.prune_stale_offsets(live_ids | bound_ids | state_ids)
    except OSError:
        logger.exception("Error during sync fix operations")

    await _sync_live_topic_names(client, live_ids)

    await _adopt_orphaned_windows(client, pre_audit.issues)
    closed_count, manual_close_count = await _close_ghost_topics(
        client, pre_audit.issues
    )
    recreated_count = await _recreate_dead_topics(client, pre_audit.issues)

    post_audit = await _run_audit()
    post_dead = await _probe_dead_topics(client)
    post_audit.issues.extend(post_dead)
    actual_fixed = pre_audit.fixable_count - post_audit.fixable_count
    text, keyboard = _format_report(
        post_audit,
        fixed_count=actual_fixed,
        closed_topic_count=closed_count,
        recreated_topic_count=recreated_count,
        manual_close_count=manual_close_count,
    )
    await safe_edit(query, text, reply_markup=keyboard)


async def handle_sync_dismiss(query: CallbackQuery) -> None:
    if query.message is None:
        return
    try:
        await query.delete_message()
        return
    except TelegramError:
        pass
    original_text = getattr(query.message, "text", None)
    await safe_edit(query, original_text or "Dismissed", reply_markup=None)


@register(CB_SYNC_FIX, CB_SYNC_DISMISS)
async def _dispatch(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    if query.data == CB_SYNC_FIX:
        await query.answer("Running fix...")
        await handle_sync_fix(query)
    elif query.data == CB_SYNC_DISMISS:
        await query.answer("Dismissed")
        await handle_sync_dismiss(query)
