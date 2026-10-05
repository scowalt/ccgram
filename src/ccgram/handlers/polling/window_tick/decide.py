from __future__ import annotations

import time

from ....providers.base import StatusUpdate
from ....terminal_parser import status_emoji_prefix
from ..polling_types import (
    STARTUP_TIMEOUT,
    TickContext,
    TickDecision,
    is_shell_prompt,
)


def build_status_line(status: StatusUpdate | None) -> str | None:
    if not status or status.is_interactive:
        return None
    if "\n" in status.raw_text:
        return status.raw_text
    return f"{status_emoji_prefix(status.raw_text)} {status.raw_text}"


def decide_tick(ctx: TickContext) -> TickDecision:
    if ctx.is_dead_window:
        return TickDecision(show_recovery=True)

    if ctx.resolved_status_text:
        return TickDecision(
            send_status=True,
            status_text=ctx.resolved_status_text,
            transition="active",
        )

    if ctx.is_recently_active:
        return TickDecision(transition="active")

    if ctx.is_shell_prompt:
        if ctx.supports_hook:
            return TickDecision(transition="done")
        return TickDecision(transition="idle")

    if ctx.has_seen_status:
        return TickDecision(transition="idle")

    startup_expired = (
        ctx.startup_time is not None
        and (time.monotonic() - ctx.startup_time) >= STARTUP_TIMEOUT
    )
    if startup_expired:
        return TickDecision(transition="idle")

    return TickDecision(transition="starting")


__all__ = ["build_status_line", "decide_tick", "is_shell_prompt"]
