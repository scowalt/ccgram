from __future__ import annotations

import contextlib
import time

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from ...config import config
from ...expandable_quote import format_expandable_quote
from ...session_state_ports.live_session_state import get_task_snapshot, get_wait_header
from ...telegram_client import TelegramClient
from ...thread_router import thread_router
from ...topic_state_registry import topic_state
from ...window_state_ports.pane_state import PaneProjection, list_pane_projections

from ..callback_data import (
    CB_STATUS_ESC,
    CB_STATUS_GET_FILE,
    CB_STATUS_LAST_REPLY,
    CB_STATUS_RECALL,
    CB_STATUS_SCREENSHOT,
    IDLE_STATUS_TEXT,
)
from ..messaging_pipeline.message_sender import edit_with_fallback, safe_send
from ..messaging_pipeline.message_task import (
    StatusClearTask,
    StatusUpdateTask,
    thread_key,
)

logger = structlog.get_logger()


_status_msg_info: dict[tuple[int, int], tuple[int, str, str, int]] = {}

_last_content_sent_at: dict[tuple[int, int], float] = {}
_STATUS_RECREATE_COOLDOWN_SECS = 10.0


def build_status_keyboard(
    window_id: str,
    history: list[str] | None = None,
    *,
    user_id: int | None = None,
) -> InlineKeyboardMarkup:
    from ..command_history import truncate_for_display

    from .status_bar_actions import build_dashboard_button

    rows: list[list[InlineKeyboardButton]] = []

    if history:
        hist_row: list[InlineKeyboardButton] = []
        for idx, cmd in enumerate(history[:2]):
            label = truncate_for_display(cmd, 20)
            hist_row.append(
                InlineKeyboardButton(
                    f"\u2191 {label}",
                    callback_data=f"{CB_STATUS_RECALL}{window_id}:{idx}"[:64],
                )
            )
        rows.append(hist_row)

    rows.append(
        [
            InlineKeyboardButton(
                "\u238b Esc",
                callback_data=f"{CB_STATUS_ESC}{window_id}"[:64],
            ),
            InlineKeyboardButton(
                "\U0001f4f8",
                callback_data=f"{CB_STATUS_SCREENSHOT}{window_id}"[:64],
            ),
            InlineKeyboardButton(
                "\U0001f4c4 Last",
                callback_data=f"{CB_STATUS_LAST_REPLY}{window_id}"[:64],
            ),
            InlineKeyboardButton(
                "\U0001f4e5 Get File",
                callback_data=f"{CB_STATUS_GET_FILE}{window_id}"[:64],
            ),
        ]
    )
    if user_id is not None:
        dashboard = build_dashboard_button(window_id, user_id)
        if dashboard is not None:
            rows.append([dashboard])
    return InlineKeyboardMarkup(rows)


def _is_idle_status_text(status_text: str) -> bool:
    return status_text.split("\n", 1)[0] == IDLE_STATUS_TEXT


def _get_idle_history(
    user_id: int, thread_id_or_0: int, status_text: str
) -> list[str] | None:
    from ..command_history import get_history

    if not _is_idle_status_text(status_text):
        return None
    return get_history(user_id, thread_id_or_0, limit=2) or None


def note_content_sent(user_id: int, thread_id_or_0: int) -> None:
    _last_content_sent_at[(user_id, thread_id_or_0)] = time.monotonic()


def _should_skip_new_status(
    user_id: int, thread_id_or_0: int, status_text: str
) -> bool:
    if _is_idle_status_text(status_text):
        if config.diagnostic_logs:
            logger.debug(
                "Skipping new idle status bubble for user %s thread %s",
                user_id,
                thread_id_or_0,
            )
        return True

    last_content_at = _last_content_sent_at.get((user_id, thread_id_or_0))
    if last_content_at is None:
        return False

    since_content = time.monotonic() - last_content_at
    if since_content >= _STATUS_RECREATE_COOLDOWN_SECS:
        return False

    if config.diagnostic_logs:
        logger.debug(
            "Skipping new status bubble for user %s thread %s; recent content %.2fs ago",
            user_id,
            thread_id_or_0,
            since_content,
        )
    return True


@topic_state.register("topic")
def clear_recent_content_for_topic(user_id: int, thread_id: int | None = None) -> None:
    _last_content_sent_at.pop((user_id, thread_key(thread_id)), None)


_PANE_BLOCK_EXPAND_THRESHOLD = 4
_PANE_BLOCKED_GLYPH = "⏸"
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60


def _format_pane_idle_age(pane: PaneProjection, now_wall: float) -> str:
    if not pane.last_active_ts:
        return "idle"
    delta = max(0.0, now_wall - pane.last_active_ts)
    if delta < _SECONDS_PER_MINUTE:
        return "idle"
    minutes = int(delta // _SECONDS_PER_MINUTE)
    if minutes < _MINUTES_PER_HOUR:
        return f"idle {minutes}m"
    hours = int(minutes // _MINUTES_PER_HOUR)
    return f"idle {hours}h"


def _format_pane_item(pane: PaneProjection, now_wall: float) -> str:
    label = pane.name.strip() if pane.name and pane.name.strip() else pane.pane_id
    if pane.state == "active":
        return f"{label} active"
    if pane.state == "blocked":
        return f"{label} {_PANE_BLOCKED_GLYPH} blocked"
    if pane.state == "dead":
        return f"{label} dead"
    return f"{label} {_format_pane_idle_age(pane, now_wall)}"


def format_pane_block(window_id: str) -> str | None:
    projections = list_pane_projections(window_id)
    if len(projections) <= 1:
        return None
    visible = [p for p in projections if p.state != "dead"]
    if len(visible) <= 1:
        return None
    visible.sort(
        key=lambda p: (
            (0, int(p.pane_id[1:]), p.pane_id)
            if p.pane_id.startswith("%") and p.pane_id[1:].isdigit()
            else (1, 0, p.pane_id)
        )
    )
    now_wall = time.time()
    items = [_format_pane_item(p, now_wall) for p in visible]
    if len(visible) >= _PANE_BLOCK_EXPAND_THRESHOLD:
        body = "\n".join(f"└ {item}" for item in items)
        return format_expandable_quote(body)
    return "└ " + " · ".join(items)


_TASK_STATUS_GLYPHS = {
    "completed": "\u2714",
    "in_progress": "\u25d4",
}
_TASK_DEFAULT_GLYPH = "\u25fb"
_VISIBLE_TASK_LIMIT = 8


def _format_task_lines(snapshot: object) -> list[str]:
    total = getattr(snapshot, "total_count", 0)
    done = getattr(snapshot, "done_count", 0)
    open_count = getattr(snapshot, "open_count", 0)
    items = list(getattr(snapshot, "items", []))
    visible_items = items[:_VISIBLE_TASK_LIMIT]
    lines: list[str] = [f"{total} tasks ({done} done, {open_count} open)"]
    for item in visible_items:
        glyph = _TASK_STATUS_GLYPHS.get(item.status, _TASK_DEFAULT_GLYPH)
        label = (
            item.active_form
            if item.status == "in_progress" and item.active_form
            else item.subject
        )
        if item.owner:
            label = f"{label} ({item.owner})"
        line = f"{glyph} #{item.task_id} {label}".rstrip()
        if item.blocked_by:
            blocked = ", ".join(f"#{task_id}" for task_id in item.blocked_by)
            line = f"{line} blocked by {blocked}"
        lines.append(line)
    hidden_count = total - len(visible_items)
    if hidden_count > 0:
        lines.append(f"+{hidden_count} more")
    return lines


def format_claude_task_status(window_id: str, base_text: str | None) -> str | None:
    snapshot = get_task_snapshot(window_id)
    wait_header = get_wait_header(window_id)
    pane_block = format_pane_block(window_id)
    if snapshot is None and not wait_header and pane_block is None:
        return base_text

    lines: list[str] = []
    header = wait_header or base_text
    if header:
        lines.append(header)
    if pane_block is not None:
        lines.append(pane_block)
    if snapshot is not None:
        lines.extend(_format_task_lines(snapshot))
    return "\n".join(lines) if lines else base_text


async def send_status_text(
    client: TelegramClient,
    user_id: int,
    thread_id_or_0: int,
    window_id: str,
    text: str,
) -> None:
    skey = (user_id, thread_id_or_0)
    thread_id: int | None = thread_id_or_0 if thread_id_or_0 != 0 else None
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)

    history = _get_idle_history(user_id, thread_id_or_0, text)
    keyboard = build_status_keyboard(
        window_id,
        history=history,
        user_id=user_id,
    )

    existing = _status_msg_info.get(skey)
    if existing:
        msg_id, stored_wid, last_text, stored_chat_id = existing
        if stored_wid == window_id and text == last_text:
            return
        if stored_wid == window_id:
            success = await edit_with_fallback(
                client, stored_chat_id, msg_id, text, reply_markup=keyboard
            )
            if success:
                _status_msg_info[skey] = (msg_id, window_id, text, stored_chat_id)
                return
            with contextlib.suppress(TelegramError):
                await client.delete_message(chat_id=stored_chat_id, message_id=msg_id)
            _status_msg_info.pop(skey, None)
        else:
            await clear_status_message(client, user_id, thread_id_or_0)
    elif _should_skip_new_status(user_id, thread_id_or_0, text):
        return

    msg = await safe_send(
        client,
        chat_id,
        text,
        message_thread_id=thread_id,
        reply_markup=keyboard,
    )
    if msg is not None:
        _status_msg_info[skey] = (msg.message_id, window_id, text, chat_id)


async def clear_status_message(
    client: TelegramClient,
    user_id: int,
    thread_id_or_0: int = 0,
) -> None:
    skey = (user_id, thread_id_or_0)
    info = _status_msg_info.pop(skey, None)
    if info:
        msg_id, _, _, chat_id = info
        try:
            await client.delete_message(chat_id=chat_id, message_id=msg_id)
        except TelegramError as e:
            logger.debug("Failed to delete status message %s: %s", msg_id, e)


async def convert_status_to_content(
    client: TelegramClient,
    user_id: int,
    thread_id_or_0: int,
    window_id: str,
    content_text: str,
) -> int | None:
    skey = (user_id, thread_id_or_0)
    info = _status_msg_info.pop(skey, None)
    if not info:
        return None

    msg_id, stored_wid, _, chat_id = info
    if stored_wid != window_id:
        with contextlib.suppress(TelegramError):
            await client.delete_message(chat_id=chat_id, message_id=msg_id)
        return None

    success = await edit_with_fallback(
        client,
        chat_id,
        msg_id,
        content_text,
        reply_markup=None,
    )
    if success:
        return msg_id
    return None


async def process_status_update(
    client: TelegramClient,
    user_id: int,
    task: StatusUpdateTask,
) -> None:
    tkey = thread_key(task.thread_id)
    status_text = format_claude_task_status(task.window_id, task.text)

    if not status_text:
        await clear_status_message(client, user_id, tkey)
        return

    await send_status_text(client, user_id, tkey, task.window_id, status_text)


async def process_status_clear(
    client: TelegramClient,
    user_id: int,
    task: StatusClearTask,
) -> None:
    window_id = task.window_id or ""
    tkey = thread_key(task.thread_id)
    status_text = format_claude_task_status(window_id, None)
    if status_text and window_id:
        await send_status_text(client, user_id, tkey, window_id, status_text)
        return
    await clear_status_message(client, user_id, tkey)


def clear_status_msg_info(user_id: int, thread_id: int | None = None) -> None:
    skey = (user_id, thread_key(thread_id))
    _status_msg_info.pop(skey, None)
