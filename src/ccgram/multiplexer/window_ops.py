from __future__ import annotations

import asyncio

import structlog

from ..thread_router import thread_router
from . import multiplexer

logger = structlog.get_logger(__name__)

SEND_KEYS_TIMEOUT_SECONDS = 5.0


async def _send_to_window_once(
    window_id: str, text: str, *, raw: bool, display: str
) -> tuple[bool, str]:
    window = await multiplexer.find_window_by_id(window_id)
    if not window:
        return False, "Window not found (may have been closed)"
    success = await multiplexer.send_keys(window.window_id, text, raw=raw)
    if success:
        logger.debug("send_to_window complete: window_id=%s (%s)", window_id, display)
        return True, f"Sent to {display}"
    return False, "Failed to send keys"


async def send_to_window(
    window_id: str, text: str, *, raw: bool = False
) -> tuple[bool, str]:
    display = thread_router.get_display_name(window_id)
    logger.debug(
        "send_to_window: window_id=%s (%s), text_len=%d",
        window_id,
        display,
        len(text),
    )
    try:
        return await asyncio.wait_for(
            _send_to_window_once(window_id, text, raw=raw, display=display),
            timeout=SEND_KEYS_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "send_to_window timed out",
            window_id=window_id,
            display=display,
            timeout=SEND_KEYS_TIMEOUT_SECONDS,
        )
        return False, f"Timed out sending keys to {display}"


async def send_followup_to_window(window_id: str, text: str) -> tuple[bool, str]:
    display = thread_router.get_display_name(window_id)
    logger.debug(
        "send_followup_to_window: window_id=%s (%s), text_len=%d",
        window_id,
        display,
        len(text),
    )
    window = await multiplexer.find_window_by_id(window_id)
    if not window:
        return False, "Window not found (may have been closed)"
    if not await multiplexer.send_keys(
        window.window_id, text, enter=False, literal=True
    ):
        return False, "Failed to send follow-up text"
    await asyncio.sleep(0.5)
    if await multiplexer.send_keys(
        window.window_id, "M-Enter", enter=False, literal=False
    ):
        return True, f"Follow-up queued for {display}"
    return False, "Failed to send follow-up key"
