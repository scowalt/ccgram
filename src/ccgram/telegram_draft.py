from __future__ import annotations

import asyncio
import contextlib
import warnings
from typing import Any, Final, Literal

import structlog
from telegram import Bot, InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.warnings import PTBUserWarning

from .utils import log_throttled

warnings.filterwarnings(
    "ignore",
    message=r".*sendMessageDraft.*",
    category=PTBUserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r".*editMessageDraft.*",
    category=PTBUserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r".*finalizeMessageDraft.*",
    category=PTBUserWarning,
)

_KEEP_MARKUP: Final[Any] = object()

logger = structlog.get_logger()

__all__ = [
    "DRAFT_LEGACY",
    "DRAFT_STREAMING",
    "DRAFT_UNSET",
    "DraftStream",
    "is_draft_unavailable",
    "is_peer_draft_unsupported",
    "mark_draft_unavailable",
    "mark_peer_draft_unsupported",
    "reset_draft_state",
]


DRAFT_STREAMING: Final[str] = "streaming"
DRAFT_LEGACY: Final[str] = "legacy"
DRAFT_UNSET: Final[str] = "unset"

_MAX_LEN: Final[int] = 4096

_DEGRADE_AFTER_FAILURES: Final[int] = 2

_UNSUPPORTED_MARKERS: Final[tuple[str, ...]] = (
    "method not found",
    "method is not implemented",
    "unknown method",
    "endpoint not found",
)

_PEER_INVALID_MARKERS: Final[tuple[str, ...]] = (
    "draft_peer_invalid",
    "peer_invalid",
    "chat not found",
)


_DRAFT_UNAVAILABLE: bool = False
_DRAFT_REASON: str = ""

_UNSUPPORTED_PEERS: set[tuple[int, int | None]] = set()


def is_draft_unavailable() -> bool:
    return _DRAFT_UNAVAILABLE


def mark_draft_unavailable(reason: str = "") -> None:
    global _DRAFT_UNAVAILABLE, _DRAFT_REASON
    if not _DRAFT_UNAVAILABLE:
        _DRAFT_UNAVAILABLE = True
        _DRAFT_REASON = reason
        logger.info("Draft streaming disabled: %s", reason or "no reason given")


def draft_unavailable_reason() -> str:
    return _DRAFT_REASON


def reset_draft_state() -> None:
    global _DRAFT_UNAVAILABLE, _DRAFT_REASON
    _DRAFT_UNAVAILABLE = False
    _DRAFT_REASON = ""
    _UNSUPPORTED_PEERS.clear()


def is_peer_draft_unsupported(chat_id: int, thread_id: int | None) -> bool:
    return (chat_id, thread_id) in _UNSUPPORTED_PEERS


def mark_peer_draft_unsupported(chat_id: int, thread_id: int | None) -> None:
    _UNSUPPORTED_PEERS.add((chat_id, thread_id))


def _is_unsupported_error(exc: BadRequest) -> bool:
    msg = exc.message.lower() if exc.message else ""
    return any(m in msg for m in _UNSUPPORTED_MARKERS)


def _is_peer_invalid_error(exc: BadRequest) -> bool:
    msg = exc.message.lower() if exc.message else ""
    return any(m in msg for m in _PEER_INVALID_MARKERS)


def _retry_after_seconds(exc: RetryAfter) -> float:
    ra = exc.retry_after
    return float(ra) if isinstance(ra, int | float) else ra.total_seconds()


def _truncate(text: str) -> str:
    return text if len(text) <= _MAX_LEN else text[:_MAX_LEN]


class DraftStream:
    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        *,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = message_thread_id
        self._reply_to = reply_to_message_id
        self._reply_markup: InlineKeyboardMarkup | None = reply_markup
        self._message_id: int | None = None
        self._buffer: str = ""
        self._mode: Literal["streaming", "legacy", "unset"] = DRAFT_UNSET  # type: ignore[assignment]
        self._closed: bool = False
        self._stream_failures: int = 0

    @property
    def message_id(self) -> int | None:
        return self._message_id

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def text(self) -> str:
        return _truncate(self._buffer)

    async def start(self, initial_text: str) -> int | None:
        if self._mode != DRAFT_UNSET:
            raise RuntimeError("DraftStream.start called twice")
        if not initial_text:
            return None
        self._buffer = initial_text

        try:
            if _DRAFT_UNAVAILABLE or is_peer_draft_unsupported(
                self._chat_id, self._thread_id
            ):
                await self._start_legacy()
            else:
                await self._start_streaming()
        except (TimedOut, NetworkError) as exc:
            logger.warning("DraftStream.start transient failure: %s", exc)
            return None
        except RetryAfter as exc:
            logger.warning("DraftStream.start rate-limited: %s", exc)
            return None
        except TelegramError as exc:
            logger.warning("DraftStream.start telegram error: %s", exc)
            return None
        return self._message_id

    async def append(self, delta: str) -> None:
        self._ensure_open()
        self._buffer += delta
        await self._push_update()

    async def replace(
        self,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None | Any = _KEEP_MARKUP,
    ) -> None:
        self._ensure_open()
        self._buffer = text
        if reply_markup is not _KEEP_MARKUP:
            self._reply_markup = reply_markup
        await self._push_update()

    async def finalize(
        self,
        final_text: str | None = None,
        *,
        reply_markup: InlineKeyboardMarkup | None | Any = _KEEP_MARKUP,
    ) -> None:
        self._ensure_open()
        if final_text is not None:
            self._buffer = final_text
        if reply_markup is not _KEEP_MARKUP:
            self._reply_markup = reply_markup
        await self._push_update(final=True)
        self._closed = True

    async def abort(self) -> None:
        if self._closed or self._message_id is None:
            self._closed = True
            return
        try:
            await self._bot.delete_message(
                chat_id=self._chat_id, message_id=self._message_id
            )
        except TelegramError as exc:
            logger.warning("DraftStream.abort delete failed: %s", exc)
        self._closed = True

    def _ensure_open(self) -> None:
        if self._mode == DRAFT_UNSET:
            raise RuntimeError("DraftStream not started")
        if self._closed:
            raise RuntimeError("DraftStream already closed")

    def _send_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {}
        if self._thread_id is not None:
            kw["message_thread_id"] = self._thread_id
        if self._reply_to is not None:
            kw["reply_to_message_id"] = self._reply_to
        if self._reply_markup is not None:
            kw["reply_markup"] = self._reply_markup
        return kw

    def _markup_dict(self) -> Any | None:
        if self._reply_markup is None:
            return None
        to_dict = getattr(self._reply_markup, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        return self._reply_markup

    async def _start_streaming(self) -> None:
        data: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": self.text,
        }
        if self._thread_id is not None:
            data["message_thread_id"] = self._thread_id
        if self._reply_to is not None:
            data["reply_to_message_id"] = self._reply_to
        markup = self._markup_dict()
        if markup is not None:
            data["reply_markup"] = markup
        try:
            result = await self._bot.do_api_request("sendMessageDraft", api_kwargs=data)
        except BadRequest as exc:
            if _is_unsupported_error(exc):
                mark_draft_unavailable(f"sendMessageDraft: {exc.message}")
                await self._start_legacy()
                return
            if _is_peer_invalid_error(exc):
                mark_peer_draft_unsupported(self._chat_id, self._thread_id)
                logger.info(
                    "sendMessageDraft peer-invalid for chat=%s thread=%s — caching legacy",
                    self._chat_id,
                    self._thread_id,
                )
                await self._start_legacy()
                return
            logger.warning("sendMessageDraft BadRequest: %s — degrading", exc)
            await self._start_legacy()
            return
        except RetryAfter as exc:
            await asyncio.sleep(_retry_after_seconds(exc) + 1)
            await self._start_legacy()
            return
        except TelegramError as exc:
            logger.warning("sendMessageDraft TelegramError: %s — degrading", exc)
            await self._start_legacy()
            return

        self._message_id = _extract_message_id(result)
        if self._message_id is None:
            logger.warning("sendMessageDraft returned no message id — degrading")
            await self._start_legacy()
            return
        self._mode = DRAFT_STREAMING

    async def _start_legacy(self) -> None:
        msg = await self._bot.send_message(
            chat_id=self._chat_id,
            text=self.text,
            **self._send_kwargs(),
        )
        self._message_id = msg.message_id
        self._mode = DRAFT_LEGACY

    async def _push_update(self, *, final: bool = False) -> None:
        if self._mode == DRAFT_STREAMING:
            await self._push_streaming(final=final)
        else:
            await self._push_legacy()

    async def _push_streaming(self, *, final: bool) -> None:
        method = "finalizeMessageDraft" if final else "editMessageDraft"
        data: dict[str, Any] = {
            "chat_id": self._chat_id,
            "message_id": self._message_id,
            "text": self.text,
        }
        markup = self._markup_dict()
        data["reply_markup"] = markup if markup is not None else {"inline_keyboard": []}
        try:
            await self._bot.do_api_request(method, api_kwargs=data)
        except BadRequest as exc:
            if _is_unsupported_error(exc):
                mark_draft_unavailable(f"{method}: {exc.message}")
                await self._degrade_in_flight()
                return
            await self._handle_stream_failure(exc)
        except RetryAfter as exc:
            await asyncio.sleep(_retry_after_seconds(exc) + 1)
            await self._handle_stream_failure(exc)
        except TelegramError as exc:
            await self._handle_stream_failure(exc)

    async def _push_legacy(self) -> None:
        markup = self._reply_markup
        if markup is None:
            markup = InlineKeyboardMarkup([])
        edit_kwargs: dict[str, Any] = {"reply_markup": markup}
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=self._message_id,
                text=self.text,
                **edit_kwargs,
            )
        except BadRequest as exc:
            if "not modified" in (exc.message or "").lower():
                return
            self._warn_legacy_edit_failed(exc)
        except RetryAfter as exc:
            await asyncio.sleep(_retry_after_seconds(exc) + 1)
            with contextlib.suppress(TelegramError):
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=self._message_id,
                    text=self.text,
                    **edit_kwargs,
                )
        except TelegramError as exc:
            self._warn_legacy_edit_failed(exc)

    def _warn_legacy_edit_failed(self, exc: TelegramError) -> None:
        log_throttled(
            logger,
            f"draft-legacy-edit:{self._chat_id}:{self._message_id}",
            "DraftStream legacy edit failed: %s",
            exc,
        )

    async def _handle_stream_failure(self, exc: TelegramError) -> None:
        self._stream_failures += 1
        logger.warning(
            "DraftStream streaming update failed (%d/%d): %s",
            self._stream_failures,
            _DEGRADE_AFTER_FAILURES,
            exc,
        )
        if self._stream_failures >= _DEGRADE_AFTER_FAILURES:
            await self._degrade_in_flight()

    async def _degrade_in_flight(self) -> None:
        if self._mode == DRAFT_LEGACY:
            return
        self._mode = DRAFT_LEGACY
        await self._push_legacy()


def _extract_message_id(result: Any) -> int | None:
    if result is None:
        return None
    if isinstance(result, dict):
        mid = result.get("message_id")
        return int(mid) if mid is not None else None
    mid = getattr(result, "message_id", None)
    return int(mid) if mid is not None else None
