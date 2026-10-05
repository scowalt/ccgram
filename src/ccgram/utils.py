import asyncio
import contextlib
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog
from telegram import Bot, Message
from telegram.error import TelegramError

logger = structlog.get_logger()


_throttle_state: dict[str, tuple[float, str]] = {}


def log_throttled(
    log: Any,
    key: str,
    msg: str,
    *args: object,
    cooldown: float = 300.0,
    _clock: Callable[[], float] = time.monotonic,
) -> None:
    formatted = msg % args if args else msg
    now = _clock()
    prev = _throttle_state.get(key)
    if prev and prev[1] == formatted and (now - prev[0]) < cooldown:
        return
    _throttle_state[key] = (now, formatted)
    log.debug(msg, *args)


def log_throttle_reset(prefix: str) -> None:
    to_remove = [k for k in _throttle_state if k.startswith(prefix)]
    for k in to_remove:
        del _throttle_state[k]


def log_throttle_sweep(
    max_age: float = 600.0,
    _clock: Callable[[], float] = time.monotonic,
) -> int:
    now = _clock()
    stale = [k for k, (ts, _) in _throttle_state.items() if now - ts >= max_age]
    for k in stale:
        del _throttle_state[k]
    return len(stale)


CCGRAM_DIR_ENV = "CCGRAM_DIR"

_SCAN_LINES = 20

_SUMMARY_MAX_CHARS = 80


def ccgram_dir() -> Path:
    raw = os.environ.get(CCGRAM_DIR_ENV, "")
    if raw:
        return Path(raw)
    return Path.home() / ".ccgram"


def load_ccgram_env() -> None:
    from dotenv import load_dotenv

    for env_path in (Path(".env"), ccgram_dir() / ".env"):
        if env_path.is_file():
            load_dotenv(env_path)


def tmux_session_name() -> str:
    return os.environ.get("TMUX_SESSION_NAME", "ccgram")


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=indent)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=f".{path.name}."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def read_cwd_from_jsonl(file_path: str | Path) -> str:
    cwd, _ = read_session_metadata_from_jsonl(file_path)
    return cwd


def _extract_user_text(msg: dict[str, object]) -> str:
    content = msg.get("content", "")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text:
                    return text[:_SUMMARY_MAX_CHARS]
    elif isinstance(content, str) and content:
        return content[:_SUMMARY_MAX_CHARS]
    return ""


def _extract_metadata_from_entry(data: dict, cwd: str, summary: str) -> tuple[str, str]:
    if not cwd:
        found_cwd = data.get("cwd")
        if found_cwd and isinstance(found_cwd, str):
            cwd = found_cwd
    if not summary and data.get("type") == "user":
        msg = data.get("message", {})
        if isinstance(msg, dict):
            summary = _extract_user_text(msg)
    return cwd, summary


def read_session_metadata_from_jsonl(file_path: str | Path) -> tuple[str, str]:
    cwd = ""
    summary = ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= _SCAN_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                cwd, summary = _extract_metadata_from_entry(data, cwd, summary)
                if cwd and summary:
                    break
    except OSError:
        pass
    return cwd, summary


def detect_tmux_context() -> tuple[str | None, str | None]:
    if not os.environ.get("TMUX"):
        return None, None
    pane_id = os.environ.get("TMUX_PANE")
    if not pane_id:
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "#{session_name}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None, None
            name = result.stdout.strip()
            return (name or None), None
        except subprocess.TimeoutExpired, FileNotFoundError:
            return None, None
    try:
        result = subprocess.run(
            [
                "tmux",
                "display-message",
                "-t",
                pane_id,
                "-p",
                "#{session_name}\t#{window_id}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None, None
        parts = result.stdout.strip().split("\t", 1)
        session_name = parts[0] if parts[0] else None
        window_id = parts[1] if len(parts) > 1 and parts[1] else None
        return session_name, window_id
    except subprocess.TimeoutExpired, FileNotFoundError:
        return None, None


def check_duplicate_ccgram(session_name: str) -> str | None:
    own_pane = os.environ.get("TMUX_PANE", "")
    if not own_pane:
        return None
    try:
        result = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-s",
                "-t",
                session_name,
                "-F",
                "#{pane_id}\t#{window_id}\t#{pane_current_command}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired, FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:  # noqa: PLR2004
            continue
        pane_id, window_id, cmd = parts
        if pane_id == own_pane:
            continue
        if cmd.strip() == "ccgram":
            return (
                f"Another ccgram instance is already running in "
                f"tmux session '{session_name}' (window {window_id})"
            )
    return None


def assert_sendable(file_path: str | Path) -> None:
    try:
        real = Path(file_path).resolve()
        state_real = ccgram_dir().resolve()
    except OSError as exc:
        raise ValueError(f"cannot verify path safety: {file_path}") from exc
    if real == state_real or str(real).startswith(str(state_real) + os.sep):
        raise ValueError(f"refusing to send state file: {file_path}")


def shorten_path(full_path: str, cwd: str | None) -> str:
    if not cwd or not full_path:
        return full_path
    cwd = cwd.rstrip("/")
    if full_path.startswith(cwd + "/"):
        return os.path.relpath(full_path, cwd)
    return full_path


def task_done_callback(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task %s failed", task.get_name(), exc_info=exc)


_general_topic_pin_cache: dict[int, bool] = {}


def is_general_topic(message: Message) -> bool:
    thread_id = getattr(message, "message_thread_id", None)
    is_forum = getattr(message.chat, "is_forum", False) if message.chat else False
    return is_forum and (thread_id is None or thread_id == 1)


async def handle_general_topic_message(
    bot: Bot, message: Message, chat_id: int
) -> None:
    if not _general_topic_pin_cache.get(chat_id):
        try:
            chat_info = await bot.get_chat(chat_id)
            pinned = chat_info.pinned_message
            if pinned and pinned.from_user and pinned.from_user.id == bot.id:
                _general_topic_pin_cache[chat_id] = True
        except TelegramError:
            pass

    if _general_topic_pin_cache.get(chat_id):
        with contextlib.suppress(TelegramError):
            await message.set_reaction("🤔")
    else:
        _general_topic_pin_cache[chat_id] = True
        try:
            hint = await message.reply_text(
                "🤖 Please use a named topic. Create a new topic to start a session."
            )
            await hint.pin(disable_notification=True)
        except TelegramError:
            pass
