from __future__ import annotations

from typing import TYPE_CHECKING
import structlog
import re
from datetime import datetime, timezone
from pathlib import Path

from telegram import Message, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from ..config import config
from ..telegram_client import PTBTelegramClient
from ..window_query import view_window
from ..multiplexer.window_ops import send_to_window
from ..thread_router import thread_router
from .callback_helpers import get_thread_id
from .messaging_pipeline.message_sender import ack_reaction, safe_reply

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = structlog.get_logger()

_UPLOAD_DIR = ".ccgram-uploads"

_MAX_FILENAME_LEN = 200

_MAX_FILE_SIZE = 50 * 1024 * 1024

_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._-]")

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_MAX_CAPTION_LEN = 500


def _sanitize_filename(name: str) -> str:
    name = Path(name).name
    name = _SAFE_FILENAME_RE.sub("_", name)
    if not name.strip("."):
        name = "unnamed"
    if len(name) > _MAX_FILENAME_LEN:
        suffix = Path(name).suffix
        if len(suffix) >= _MAX_FILENAME_LEN:
            suffix = suffix[:10]
        stem = Path(name).stem[: _MAX_FILENAME_LEN - len(suffix)]
        name = stem + suffix
    return name or "unnamed"


def _sanitize_caption(text: str) -> str:
    cleaned = _CONTROL_CHAR_RE.sub("", text)
    cleaned = cleaned.replace("\n", " ").replace("\r", " ")
    return cleaned[:_MAX_CAPTION_LEN]


def _validate_dest_path(dest: Path, upload_path: Path) -> bool:
    try:
        dest.resolve().relative_to(upload_path.resolve())
        return True
    except ValueError, OSError:
        return False


def _unique_dest(dest: Path) -> Path:
    if not dest.exists() and not dest.is_symlink():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    for i in range(1, 100):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    ts = datetime.now(tz=timezone.utc).strftime("%H%M%S%f")
    return parent / f"{stem}_{ts}{suffix}"


def _generate_photo_filename(file_unique_id: str) -> str:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_id = file_unique_id[:8]
    return f"photo_{timestamp}_{short_id}.jpg"


def _resolve_upload_dir(
    user_id: int, thread_id: int | None
) -> tuple[str | None, Path | None, str | None]:
    window_id = thread_router.resolve_window_for_thread(user_id, thread_id)
    if not window_id:
        return None, None, "No session bound to this topic."

    view = view_window(window_id)
    if view is None or not view.cwd:
        return window_id, None, "Session has no working directory."

    upload_path = Path(view.cwd) / _UPLOAD_DIR
    return window_id, upload_path, None


async def _download_and_save(
    message: Message,
    upload_path: Path,
    filename: str,
    file_id: str,
    file_size: int | None,
    size_label: str,
) -> str | None:
    if file_size is not None and file_size > _MAX_FILE_SIZE:
        size_mb = file_size / (1024 * 1024)
        await safe_reply(
            message,
            f"\u274c {size_label} too large ({size_mb:.1f} MB). Maximum {_MAX_FILE_SIZE // (1024 * 1024)} MB.",
        )
        return None

    try:
        upload_path.mkdir(parents=True, exist_ok=True)
        if upload_path.is_symlink():
            logger.error("Upload dir is a symlink: %s", upload_path)
            await safe_reply(message, "\u274c Upload directory is invalid.")
            return None
        dest = upload_path / filename
        if not _validate_dest_path(dest, upload_path):
            logger.error("Path traversal attempt blocked: %s", filename)
            await safe_reply(message, "\u274c Invalid filename.")
            return None
        dest = _unique_dest(dest)
        filename = dest.name
        file = await message.get_bot().get_file(file_id)
        await file.download_to_drive(str(dest))
        actual_size = dest.stat().st_size
        if actual_size > _MAX_FILE_SIZE:
            dest.unlink(missing_ok=True)
            size_mb = actual_size / (1024 * 1024)
            await safe_reply(
                message,
                f"\u274c {size_label} too large ({size_mb:.1f} MB). Maximum {_MAX_FILE_SIZE // (1024 * 1024)} MB.",
            )
            return None
    except (OSError, TelegramError) as e:
        logger.error("Failed to save %s: %s", size_label.lower(), e)
        await safe_reply(message, "\u274c Failed to save file.")
        return None

    return filename


async def _upload_and_notify(
    message: Message,
    user_id: int,
    thread_id: int | None,
    filename: str,
    file_id: str,
    file_size: int | None,
    size_label: str,
    claude_msg_tpl: str,
    success_emoji: str,
) -> None:
    window_id, upload_path, error = _resolve_upload_dir(user_id, thread_id)
    if error or not window_id or not upload_path:
        await safe_reply(message, f"\u274c {error}")
        return

    await message.chat.send_action(ChatAction.TYPING)

    saved_name = await _download_and_save(
        message, upload_path, filename, file_id, file_size, size_label
    )
    if not saved_name:
        return

    rel_path = f"{_UPLOAD_DIR}/{saved_name}"
    caption = message.caption or ""
    claude_msg = claude_msg_tpl.format(name=saved_name, path=rel_path)
    if caption:
        claude_msg += f"\n\nUser note: {_sanitize_caption(caption)}"

    success, err = await send_to_window(window_id, claude_msg)
    if success:
        await ack_reaction(
            PTBTelegramClient(message.get_bot()), message.chat.id, message.message_id
        )
        await safe_reply(message, f"{success_emoji} Uploaded `{rel_path}`")
    else:
        await safe_reply(
            message, f"\u274c File saved but failed to notify Claude: {err}"
        )


async def handle_photo_message(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    user = update.effective_user
    message = update.message
    if not user or not message or not message.photo:
        return
    if not config.is_user_allowed(user.id):
        await safe_reply(message, "You are not authorized to use this bot.")
        return

    photo = message.photo[-1]
    await _upload_and_notify(
        message,
        user.id,
        get_thread_id(update),
        _generate_photo_filename(photo.file_unique_id),
        photo.file_id,
        photo.file_size,
        "Photo",
        "I've uploaded an image to {path} — please take a look.",
        "\U0001f4f7",
    )


async def handle_document_message(
    update: Update, _context: ContextTypes.DEFAULT_TYPE
) -> None:
    user = update.effective_user
    message = update.message
    if not user or not message or not message.document:
        return
    if not config.is_user_allowed(user.id):
        await safe_reply(message, "You are not authorized to use this bot.")
        return

    doc = message.document
    await _upload_and_notify(
        message,
        user.id,
        get_thread_id(update),
        _sanitize_filename(doc.file_name or "document"),
        doc.file_id,
        doc.file_size,
        "File",
        "I've uploaded {name} to {path}",
        "\U0001f4ce",
    )
