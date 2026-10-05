from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

from ...telegram_client import TelegramClient, unwrap_bot
from ...telegram_draft import DraftStream
from ...thread_router import thread_router
from ...topic_state_registry import topic_state
from ...window_state_ports.tool_state import get_batch_mode, is_ephemeral_tools
from .message_task import ContentTask, thread_key

logger = structlog.get_logger()

BATCH_MAX_LENGTH = 2800
BATCH_MAX_ENTRIES = 9


@dataclass
class ToolBatchEntry:
    tool_use_id: str | None
    tool_use_text: str
    tool_result_text: str | None = None
    tool_name: str | None = None


@dataclass
class ToolBatch:
    window_id: str
    thread_id: int
    entries: list[ToolBatchEntry] = field(default_factory=list)
    telegram_msg_id: int | None = None
    total_length: int = 0
    draft: DraftStream | None = None
    last_sent_text: str | None = None


_active_batches: dict[tuple[int, int], ToolBatch] = {}

_MARKDOWN_TOOL_PREFIX_RE = re.compile(r"^\*\*([^*]+)\*\*(.*)$")
_PLAIN_TASK_CREATE_RE = re.compile(r"^TaskCreate\s+(.+)$")
_TASK_TOOL_NAMES = frozenset({"TaskCreate", "TaskUpdate", "TaskList"})
_MIN_BACKTICK_WRAPPED_LENGTH = 2


def is_batch_eligible(task: ContentTask) -> bool:
    return task.content_type in ("tool_use", "tool_result") and get_batch_mode(
        task.window_id
    ) in ("batched", "ephemeral")


def format_batch_message(
    entries: list[ToolBatchEntry], subagent_label: str | None = None
) -> str:
    task_create_message = _format_task_create_batch(entries, subagent_label)
    if task_create_message is not None:
        return task_create_message

    lines: list[str] = []
    if subagent_label:
        lines.append(subagent_label)
    lines.extend(_format_mixed_batch_lines(entries))

    return "\n".join(lines)


def _format_task_create_batch(
    entries: list[ToolBatchEntry], subagent_label: str | None
) -> str | None:
    if not entries or any(entry.tool_name != "TaskCreate" for entry in entries):
        return None

    titles = [_extract_task_create_title(entry) for entry in entries]
    if any(not title for title in titles):
        return None

    action = (
        "Created"
        if all(entry.tool_result_text is not None for entry in entries)
        else "Creating"
    )
    task_label = "task" if len(entries) == 1 else "tasks"
    lines: list[str] = []
    if subagent_label:
        lines.append(subagent_label)
    if action == "Creating":
        lines.append(f"{action} {len(entries)} {task_label}\u2026")
    else:
        lines.append(f"{action} {len(entries)} {task_label}")
    lines.extend(f"{index}. {title}" for index, title in enumerate(titles, start=1))
    return "\n".join(lines)


def _collect_standard_run(
    entries: list[ToolBatchEntry], start: int
) -> tuple[list[ToolBatchEntry], int]:
    first = entries[start]
    run_text = first.tool_use_text
    run_bucket = first.tool_result_text
    run: list[ToolBatchEntry] = [first]
    index = start + 1
    while index < len(entries) and entries[index].tool_name not in _TASK_TOOL_NAMES:
        nxt = entries[index]
        if nxt.tool_use_text != run_text or nxt.tool_result_text != run_bucket:
            break
        run.append(nxt)
        index += 1
    return run, index


def _format_mixed_batch_lines(entries: list[ToolBatchEntry]) -> list[str]:
    lines: list[str] = []
    index = 0

    while index < len(entries):
        entry = entries[index]
        if entry.tool_name == "TaskCreate":
            task_entries: list[ToolBatchEntry] = []
            while index < len(entries) and entries[index].tool_name == "TaskCreate":
                task_entries.append(entries[index])
                index += 1
            section = _format_task_create_section(task_entries)
            if section:
                lines.extend(section)
            else:
                lines.extend(_format_batch_entry(task) for task in task_entries)
            continue
        if entry.tool_name == "TaskUpdate":
            update_entries: list[ToolBatchEntry] = []
            while index < len(entries) and entries[index].tool_name == "TaskUpdate":
                update_entries.append(entries[index])
                index += 1
            section = _format_task_update_section(update_entries)
            if section:
                lines.extend(section)
            else:
                lines.extend(_format_batch_entry(task) for task in update_entries)
            continue
        if entry.tool_name == "TaskList":
            lines.extend(_format_task_list_section(entry))
            index += 1
            continue

        run, index = _collect_standard_run(entries, index)
        lines.append(_format_batch_entry(run[-1], count=len(run)))

    return lines


def _format_task_create_section(entries: list[ToolBatchEntry]) -> list[str]:
    if not entries:
        return []

    titles = [_extract_task_create_title(entry) for entry in entries]
    if any(not title for title in titles):
        return []

    action = (
        "Created"
        if all(entry.tool_result_text is not None for entry in entries)
        else "Creating"
    )
    task_label = "task" if len(entries) == 1 else "tasks"
    heading = (
        f"{action} {len(entries)} {task_label}\u2026"
        if action == "Creating"
        else f"{action} {len(entries)} {task_label}"
    )
    return [
        heading,
        *(f"{index}. {title}" for index, title in enumerate(titles, start=1)),
    ]


def _format_task_update_section(entries: list[ToolBatchEntry]) -> list[str]:
    if not entries:
        return []

    labels = [_extract_task_tool_suffix(entry) for entry in entries]
    if any(not label for label in labels):
        return []

    action = (
        "Updated"
        if all(entry.tool_result_text is not None for entry in entries)
        else "Updating"
    )
    task_label = "task" if len(entries) == 1 else "tasks"
    heading = (
        f"{action} {len(entries)} {task_label}\u2026"
        if action == "Updating"
        else f"{action} {len(entries)} {task_label}"
    )
    return [heading, *(f"- {label}" for label in labels)]


def _format_task_list_section(entry: ToolBatchEntry) -> list[str]:
    summary = _extract_task_tool_suffix(entry)
    heading = (
        "Synced task list"
        if entry.tool_result_text is not None
        else "Refreshing task list\u2026"
    )
    if summary and summary != "refresh":
        heading = f"{heading} ({summary})"
    return [heading]


def _format_batch_entry(entry: ToolBatchEntry, count: int = 1) -> str:
    line = entry.tool_use_text
    if count > 1:
        line = f"{line} \u00d7{count}"
    return line


def _extract_task_create_title(entry: ToolBatchEntry) -> str:
    return _extract_task_tool_suffix(entry)


def _extract_task_tool_suffix(entry: ToolBatchEntry) -> str:
    text = entry.tool_use_text.strip()
    if not text:
        return ""

    if ": " in text:
        _, _, suffix = text.partition(": ")
        suffix = suffix.strip()
        if (
            suffix.startswith("`")
            and suffix.endswith("`")
            and len(suffix) >= _MIN_BACKTICK_WRAPPED_LENGTH
        ):
            suffix = suffix[1:-1].strip()
        if suffix:
            return suffix

    markdown_match = _MARKDOWN_TOOL_PREFIX_RE.match(text)
    if markdown_match:
        _tool_name, suffix = markdown_match.groups()
        stripped = suffix.strip()
        if (
            stripped.startswith("`")
            and stripped.endswith("`")
            and len(stripped) >= _MIN_BACKTICK_WRAPPED_LENGTH
        ):
            stripped = stripped[1:-1].strip()
        return stripped

    plain_match = _PLAIN_TASK_CREATE_RE.match(text)
    if plain_match:
        return plain_match.group(1).strip()

    return text


async def _send_or_edit_batch(
    client: TelegramClient,
    user_id: int,
    batch: ToolBatch,
    chat_id: int,
    raw_thread_id: int | None,
    thread_id_or_0: int,
) -> None:
    from ...claude_task_state import build_subagent_label, get_subagent_names

    from ..status.status_bubble import clear_status_message

    subagent_label = build_subagent_label(get_subagent_names(batch.window_id))
    batch_text = format_batch_message(batch.entries, subagent_label=subagent_label)

    if batch.telegram_msg_id is not None and batch.last_sent_text == batch_text:
        return

    if is_ephemeral_tools(batch.window_id):
        from .message_sender import edit_with_fallback, safe_send

        if batch.telegram_msg_id is None:
            await clear_status_message(client, user_id, thread_id_or_0)
            await _rate_limit_chat(chat_id)
            msg = await safe_send(
                client, chat_id, batch_text, message_thread_id=raw_thread_id
            )
            if msg is not None:
                batch.telegram_msg_id = msg.message_id
                batch.last_sent_text = batch_text
        else:
            success = await edit_with_fallback(
                client, chat_id, batch.telegram_msg_id, batch_text
            )
            if success:
                batch.last_sent_text = batch_text
        return

    if batch.draft is None:
        await clear_status_message(client, user_id, thread_id_or_0)
        await _rate_limit_chat(chat_id)
        batch.draft = DraftStream(
            unwrap_bot(client),
            chat_id,
            message_thread_id=raw_thread_id,
        )
        msg_id = await batch.draft.start(batch_text)
        if msg_id is not None:
            batch.telegram_msg_id = msg_id
            batch.last_sent_text = batch_text
        else:
            batch.draft = None
    else:
        await batch.draft.replace(batch_text)
        batch.last_sent_text = batch_text


async def _rate_limit_chat(chat_id: int) -> None:
    from .message_sender import rate_limit_send

    await rate_limit_send(chat_id)


async def _handle_tool_result(
    client: TelegramClient,
    user_id: int,
    task: ContentTask,
    batch: ToolBatch | None,
    thread_id_or_0: int,
) -> tuple[ToolBatch | None, ContentTask | None]:
    if not task.tool_use_id or not batch:
        return None, task
    for entry in batch.entries:
        if entry.tool_use_id == task.tool_use_id:
            text = "\n".join(task.parts) if task.parts else ""
            first_line = text.split("\n", 1)[0][:72]
            entry.tool_result_text = first_line
            return batch, None
    if is_ephemeral_tools(batch.window_id):
        return None, None
    await flush_batch(client, user_id, thread_id_or_0)
    return None, task


def _add_tool_use_entry(
    task: ContentTask,
    batch: ToolBatch,
    ephemeral: bool = False,
) -> bool:
    entry_text = "\n".join(task.parts) if task.parts else "tool call"
    if ephemeral:
        if len(entry_text) > BATCH_MAX_LENGTH:
            entry_text = entry_text[: BATCH_MAX_LENGTH - 1] + "…"
        while batch.entries and (
            len(batch.entries) >= BATCH_MAX_ENTRIES
            or batch.total_length + len(entry_text) > BATCH_MAX_LENGTH
        ):
            dropped = batch.entries.pop(0)
            batch.total_length -= len(dropped.tool_use_text)
        entry = ToolBatchEntry(
            tool_use_id=task.tool_use_id,
            tool_use_text=entry_text,
            tool_name=task.tool_name,
        )
        batch.entries.append(entry)
        batch.total_length += len(entry_text)
        return False
    if (
        len(batch.entries) >= BATCH_MAX_ENTRIES
        or batch.total_length + len(entry_text) > BATCH_MAX_LENGTH
    ):
        return True
    entry = ToolBatchEntry(
        tool_use_id=task.tool_use_id,
        tool_use_text=entry_text,
        tool_name=task.tool_name,
    )
    batch.entries.append(entry)
    batch.total_length += len(entry_text)
    return False


async def process_tool_event(
    client: TelegramClient,
    user_id: int,
    task: ContentTask,
) -> ContentTask | None:
    window_id = task.window_id
    thread_id_or_0 = thread_key(task.thread_id)
    bkey = (user_id, thread_id_or_0)
    chat_id = thread_router.resolve_chat_id(user_id, task.thread_id)
    batch = _active_batches.get(bkey)

    if task.content_type == "tool_result":
        batch, followup = await _handle_tool_result(
            client, user_id, task, batch, thread_id_or_0
        )
        if batch is None:
            return followup
    elif task.content_type == "tool_use":
        result = await _handle_tool_use_event(
            client, user_id, task, batch, window_id, thread_id_or_0, bkey
        )
        if isinstance(result, ContentTask):
            return result
        if result is None:
            return None
        batch = result
    else:
        return task

    await _send_or_edit_batch(
        client, user_id, batch, chat_id, task.thread_id, thread_id_or_0
    )
    return None


async def _handle_tool_use_event(
    client: TelegramClient,
    user_id: int,
    task: ContentTask,
    batch: ToolBatch | None,
    window_id: str,
    thread_id_or_0: int,
    bkey: tuple[int, int],
) -> ToolBatch | ContentTask | None:
    if batch and batch.window_id != window_id:
        await flush_batch(client, user_id, thread_id_or_0)
        batch = None

    if not batch:
        batch = ToolBatch(window_id=window_id, thread_id=thread_id_or_0)
        _active_batches[bkey] = batch

    ephemeral = is_ephemeral_tools(window_id)
    overflow = _add_tool_use_entry(task, batch, ephemeral=ephemeral)
    if overflow:
        await flush_batch(client, user_id, thread_id_or_0)
        batch = ToolBatch(window_id=window_id, thread_id=thread_id_or_0)
        still_overflow = _add_tool_use_entry(task, batch)
        _active_batches[bkey] = batch
        if still_overflow:
            _active_batches.pop(bkey, None)
            return task

    return batch


async def flush_if_active(
    client: TelegramClient, user_id: int, task: ContentTask
) -> None:
    thread_id_or_0 = thread_key(task.thread_id)
    if has_active_batch(user_id, thread_id_or_0):
        await flush_batch(client, user_id, thread_id_or_0)


async def flush_batch(
    client: TelegramClient, user_id: int, thread_id_or_0: int
) -> None:
    from telegram.error import TelegramError

    bkey = (user_id, thread_id_or_0)
    batch = _active_batches.pop(bkey, None)
    if not batch or not batch.entries:
        return

    thread_id: int | None = thread_id_or_0 if thread_id_or_0 != 0 else None
    chat_id = thread_router.resolve_chat_id(user_id, thread_id)

    if is_ephemeral_tools(batch.window_id):
        if batch.telegram_msg_id is not None:
            try:
                await client.delete_message(
                    chat_id=chat_id, message_id=batch.telegram_msg_id
                )
            except TelegramError as exc:
                logger.warning("flush_batch ephemeral delete failed: %s", exc)
        return

    from ...claude_task_state import build_subagent_label, get_subagent_names

    subagent_label = build_subagent_label(get_subagent_names(batch.window_id))
    batch_text = format_batch_message(batch.entries, subagent_label=subagent_label)

    if batch.draft is not None and not batch.draft.closed:
        try:
            await batch.draft.finalize(batch_text)
        except TelegramError as exc:
            logger.warning("flush_batch finalize failed: %s", exc)
        return

    if batch.telegram_msg_id is not None:
        try:
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=batch.telegram_msg_id,
                text=batch_text,
            )
        except TelegramError as exc:
            logger.warning("flush_batch edit failed: %s", exc)
        return

    await _rate_limit_chat(chat_id)
    draft = DraftStream(unwrap_bot(client), chat_id, message_thread_id=thread_id)
    try:
        await draft.start(batch_text)
        await draft.finalize()
    except TelegramError as exc:
        logger.warning("flush_batch start+finalize failed: %s", exc)


def has_active_batch(user_id: int, thread_id_or_0: int) -> bool:
    return (user_id, thread_id_or_0) in _active_batches


def has_ephemeral_active_batch(user_id: int, thread_id_or_0: int) -> bool:
    batch = _active_batches.get((user_id, thread_id_or_0))
    return batch is not None and is_ephemeral_tools(batch.window_id)


@topic_state.register("topic")
def clear_batch_for_topic(user_id: int, thread_id: int | None = None) -> None:
    _active_batches.pop((user_id, thread_key(thread_id)), None)


def clear_all_batches() -> None:
    _active_batches.clear()
