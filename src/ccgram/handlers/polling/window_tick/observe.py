from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from .... import window_query
from ....providers import get_provider_for_window
from ....providers.base import StatusUpdate
from ....session_monitor import get_active_monitor
from ....multiplexer import agent_status_cache
from ....multiplexer import multiplexer as tmux_manager
from ....multiplexer.vim_state import has_insert_indicator, notify_vim_insert_seen
from ..polling_state import terminal_poll_state, terminal_screen_buffer
from ..polling_types import TickContext, is_shell_prompt
from .decide import build_status_line

if TYPE_CHECKING:
    from ....providers.base import AgentProvider
    from ....multiplexer.base import WindowRef as TmuxWindow
    from ..polling_runtime import PollingRuntime

logger = structlog.get_logger()


def _get_provider(window_id: str) -> "AgentProvider":
    return get_provider_for_window(
        window_id, provider_name=window_query.get_window_provider(window_id)
    )


def _parse_with_pyte(
    window_id: str,
    pane_text: str,
    columns: int = 0,
    rows: int = 0,
    *,
    parse_claude_chrome: bool = True,
    runtime: "PollingRuntime | None" = None,
) -> StatusUpdate | None:
    sb = runtime.screen_buffer if runtime is not None else terminal_screen_buffer
    return sb.parse_with_pyte(
        window_id, pane_text, columns, rows, parse_claude_chrome=parse_claude_chrome
    )


def _check_vim_insert(
    window_id: str,
    pane_text: str,
    w: "TmuxWindow",
    runtime: "PollingRuntime | None" = None,
) -> None:
    sb = runtime.screen_buffer if runtime is not None else terminal_screen_buffer
    vim_text = sb.get_rendered_text(window_id, pane_text)
    if has_insert_indicator(vim_text):
        notify_vim_insert_seen(w.window_id)


def _get_last_activity_ts(window_id: str) -> float | None:
    session_id = window_query.get_session_id_for_window(window_id)
    if not session_id:
        return None
    mon = get_active_monitor()
    return mon.get_last_activity(session_id) if mon else None


async def _resolve_status(
    window_id: str,
    pane_text: str,
    w: "TmuxWindow",
    runtime: "PollingRuntime | None" = None,
) -> StatusUpdate | None:
    sb = runtime.screen_buffer if runtime is not None else terminal_screen_buffer
    provider = _get_provider(window_id)
    status = _parse_with_pyte(
        window_id,
        pane_text,
        columns=w.pane_width,
        rows=w.pane_height,
        parse_claude_chrome=provider.capabilities.uses_pyte_status_parsing,
        runtime=runtime,
    )
    if status is not None:
        return status
    clean_text = sb.get_rendered_text(window_id, pane_text)
    pane_title = ""
    if provider.capabilities.uses_pane_title:
        pane_title = await tmux_manager.get_pane_title(w.window_id)
    status = provider.parse_terminal_status(clean_text, pane_title=pane_title)
    if status is not None:
        return status
    return await _native_agent_status(window_id)


async def _native_agent_status(window_id: str) -> StatusUpdate | None:
    if not tmux_manager.capabilities.native_agent_status:
        return None
    native = agent_status_cache.get_status(window_id)
    if native is None:
        native = await tmux_manager.agent_status(window_id)
    if native is None:
        return None
    if native.state == "working":
        label = native.custom_status or "working"
        return StatusUpdate(raw_text=label, display_label=label)
    if native.state == "blocked":
        return StatusUpdate(raw_text="waiting for input", display_label="waiting")
    return None


def build_context(
    window_id: str,
    w: "TmuxWindow",
    status: StatusUpdate | None,
    runtime: "PollingRuntime | None" = None,
) -> TickContext:
    ps = runtime.poll_state if runtime is not None else terminal_poll_state
    last_activity_ts = _get_last_activity_ts(window_id)
    is_recently_active = ps.is_recently_active(window_id, last_activity_ts)
    resolved_status_text = build_status_line(status)
    ws = ps.peek_state(window_id)
    provider = _get_provider(window_id)
    return TickContext(
        window_id=window_id,
        resolved_status_text=resolved_status_text,
        is_shell_prompt=is_shell_prompt(w.pane_current_command),
        has_seen_status=ps.check_seen_status(window_id),
        is_recently_active=is_recently_active,
        startup_time=ws.startup_time if ws else None,
        is_dead_window=False,
        supports_hook=provider.capabilities.supports_hook,
    )


__all__ = [
    "_check_vim_insert",
    "_get_last_activity_ts",
    "_get_provider",
    "_parse_with_pyte",
    "_resolve_status",
    "build_context",
]
