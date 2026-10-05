from __future__ import annotations

import re
import time
import zlib
from collections.abc import Iterable
from typing import TYPE_CHECKING

import structlog

from ...providers.base import StatusUpdate
from ...topic_state_registry import topic_state
from .polling_types import (
    MAX_PROBE_FAILURES,
    PANE_COUNT_TTL,
    RC_DEBOUNCE_SECONDS,
    STARTUP_TIMEOUT,
    TYPING_INTERVAL,
    BlockedAlertCallback,
    PaneOutputCallback,
    PaneStateName,
    PaneTransition,
    TopicPollState,
    WindowPollState,
)
from .polling_types import ACTIVITY_THRESHOLD as _ACTIVITY_THRESHOLD

if TYPE_CHECKING:
    from telegram import Bot

    from ...providers.base import AgentProvider
    from ...screen_buffer import ScreenBuffer
    from ...multiplexer.base import PaneInfo as TmuxPaneInfo

logger = structlog.get_logger()

_HOOK_RUNNER_LOG_RE = re.compile(
    r"^(?:\x1b\[[0-9;]*m)*"
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} "
    r"\[\s*(?:debug|info|warning|error|critical)\s*\]"
)


def _strip_hook_runner_noise(pane_text: str) -> str:
    if "] " not in pane_text:
        return pane_text
    lines = pane_text.split("\n")
    kept = [ln for ln in lines if not _HOOK_RUNNER_LOG_RE.match(ln)]
    if len(kept) == len(lines):
        return pane_text
    return "\n".join(kept)


class TerminalScreenBuffer:
    def __init__(self, poll_state: TerminalPollState) -> None:
        self._poll_state = poll_state
        topic_state.register_bound("window", self.clear_screen_buffer)

    def clear_screen_buffer(self, window_id: str) -> None:
        ws = self._poll_state.peek_state(window_id)
        if ws:
            ws.screen_buffer = None
            ws.pane_count_cache = None
            ws.last_pane_hash = None
            ws.last_pyte_result = None
            ws.last_rendered_text = None

    def reset_screen_buffer_state(self) -> None:
        for ws in self._poll_state.iter_states():
            ws.screen_buffer = None
            ws.pane_count_cache = None
            ws.last_pane_hash = None
            ws.last_pyte_result = None
            ws.last_rendered_text = None
            ws.rc_active = False
            ws.rc_off_since = None

    def is_rc_active(self, window_id: str) -> bool:
        ws = self._poll_state.peek_state(window_id)
        return ws.rc_active if ws else False

    def update_rc_state(self, ws: WindowPollState, rc_detected: bool) -> None:
        if rc_detected:
            ws.rc_active = True
            ws.rc_off_since = None
        elif ws.rc_active:
            now = time.monotonic()
            if ws.rc_off_since is None:
                ws.rc_off_since = now
            elif now - ws.rc_off_since >= RC_DEBOUNCE_SECONDS:
                ws.rc_active = False
                ws.rc_off_since = None

    def update_pane_count_cache(self, window_id: str, count: int) -> None:
        self._poll_state.get_state(window_id).pane_count_cache = (
            count,
            time.monotonic() + PANE_COUNT_TTL,
        )

    def is_single_pane_cached(self, window_id: str) -> bool:
        ws = self._poll_state.peek_state(window_id)
        if not ws or not ws.pane_count_cache:
            return False
        count, expiry = ws.pane_count_cache
        return count <= 1 and expiry > time.monotonic()

    def get_rendered_text(self, window_id: str, fallback: str) -> str:
        ws = self._poll_state.peek_state(window_id)
        if ws and ws.last_rendered_text is not None:
            return ws.last_rendered_text
        return fallback

    def get_screen_buffer(
        self, window_id: str, columns: int, rows: int
    ) -> ScreenBuffer:
        from ...screen_buffer import ScreenBuffer

        ws = self._poll_state.get_state(window_id)
        buf = ws.screen_buffer
        if buf is None or not isinstance(buf, ScreenBuffer):
            buf = ScreenBuffer(columns=columns, rows=rows)
            ws.screen_buffer = buf
        elif buf.columns != columns or buf.rows != rows:
            buf.resize(columns, rows)
        else:
            buf.reset()
        return buf

    def parse_with_pyte(
        self,
        window_id: str,
        pane_text: str,
        columns: int = 0,
        rows: int = 0,
        *,
        parse_claude_chrome: bool = True,
    ) -> StatusUpdate | None:
        from ...terminal_parser import (
            detect_remote_control,
            format_status_display,
            parse_from_screen,
            parse_status_block_from_screen,
        )

        if (
            not isinstance(columns, int)
            or not isinstance(rows, int)
            or columns <= 0
            or rows <= 0
        ):
            columns, rows = 200, 50

        pane_text = _strip_hook_runner_noise(pane_text)

        ws = self._poll_state.get_state(window_id)
        content_hash = hash((pane_text, columns, rows))
        if (
            ws.last_pane_hash is not None
            and content_hash == ws.last_pane_hash
            and (ws.last_pyte_result is None or not ws.last_pyte_result.is_interactive)
        ):
            self.update_rc_state(ws, ws.last_rc_detected)
            return ws.last_pyte_result

        buf = self.get_screen_buffer(window_id, columns, rows)
        buf.feed(pane_text)
        ws.last_rendered_text = buf.rendered_text

        rc_detected = detect_remote_control(buf.display)
        ws.last_rc_detected = rc_detected
        self.update_rc_state(ws, rc_detected)

        if not parse_claude_chrome:
            ws.last_pane_hash = content_hash
            ws.last_pyte_result = None
            return None

        interactive = parse_from_screen(buf)
        if interactive:
            result = StatusUpdate(
                raw_text=interactive.content,
                display_label=interactive.name,
                is_interactive=True,
                ui_type=interactive.name,
            )
            ws.last_pane_hash = content_hash
            ws.last_pyte_result = result
            return result

        raw_status = parse_status_block_from_screen(buf)
        if raw_status:
            headline = raw_status.split("\n", 1)[0]
            result = StatusUpdate(
                raw_text=raw_status,
                display_label=format_status_display(headline),
            )
            ws.last_pane_hash = content_hash
            ws.last_pyte_result = result
            return result

        ws.last_pane_hash = content_hash
        ws.last_pyte_result = None
        return None


class TerminalPollState:
    def __init__(self) -> None:
        self._states: dict[str, WindowPollState] = {}
        topic_state.register_bound("window", self.clear_state)

    def get_state(self, window_id: str) -> WindowPollState:
        return self._states.setdefault(window_id, WindowPollState())

    def peek_state(self, window_id: str) -> WindowPollState | None:
        return self._states.get(window_id)

    def iter_states(self) -> Iterable[WindowPollState]:
        return list(self._states.values())

    def clear_state(self, window_id: str) -> None:
        self._states.pop(window_id, None)

    def clear_unbound_timers(self, bound_ids: set[str], live_ids: set[str]) -> None:
        for wid, ws in list(self._states.items()):
            if ws.unbound_timer is not None and (
                wid in bound_ids or wid not in live_ids
            ):
                ws.unbound_timer = None

    def get_expired_unbound(self, now: float, timeout: float) -> list[str]:
        return [
            wid
            for wid, ws in self._states.items()
            if ws.unbound_timer is not None and now - ws.unbound_timer >= timeout
        ]

    def get_orphaned_window_ids(
        self, live_ids: set[str], bound_ids: set[str]
    ) -> list[str]:
        return [
            wid for wid in self._states if wid not in live_ids and wid not in bound_ids
        ]

    def reset_probe_failures(self, window_id: str) -> None:
        ws = self._states.get(window_id)
        if ws:
            ws.probe_failures = 0

    def clear_seen_status(self, window_id: str) -> None:
        ws = self._states.get(window_id)
        if ws:
            ws.has_seen_status = False
            ws.startup_time = None

    def set_unbound_timer(self, window_id: str, ts: float) -> None:
        ws = self.get_state(window_id)
        ws.unbound_timer = ts

    def clear_unbound_timer(self, window_id: str) -> None:
        ws = self._states.get(window_id)
        if ws:
            ws.unbound_timer = None

    def reset_all_probe_failures(self) -> None:
        for ws in self._states.values():
            ws.probe_failures = 0

    def reset_all_seen_status(self) -> None:
        for ws in self._states.values():
            ws.has_seen_status = False
            ws.startup_time = None

    def reset_all_unbound_timers(self) -> None:
        for ws in self._states.values():
            ws.unbound_timer = None

    def cancel_startup_timer(self, window_id: str) -> None:
        ws = self._states.get(window_id)
        if ws:
            ws.startup_time = None

    def begin_startup_timer(self, window_id: str, now: float) -> None:
        self.get_state(window_id).startup_time = now

    def check_seen_status(self, window_id: str) -> bool:
        ws = self._states.get(window_id)
        return ws.has_seen_status if ws else False

    def is_recently_active(self, window_id: str, last_activity: float | None) -> bool:
        if not last_activity:
            return False
        if (time.monotonic() - last_activity) < _ACTIVITY_THRESHOLD:
            self.mark_seen_status(window_id)
            return True
        return False

    def is_startup_expired(self, window_id: str) -> bool:
        ws = self._states.get(window_id)
        if not ws or ws.startup_time is None:
            return False
        return (time.monotonic() - ws.startup_time) >= STARTUP_TIMEOUT

    def mark_seen_status(self, window_id: str) -> None:
        ws = self.get_state(window_id)
        ws.has_seen_status = True
        ws.startup_time = None


class InteractiveUIStrategy:
    def __init__(self) -> None:
        self._pane_alert_hashes: dict[str, tuple[str, float, str]] = {}
        topic_state.register_bound("window", self.clear_pane_alerts)

    def has_pane_alert(self, pane_id: str) -> bool:
        return pane_id in self._pane_alert_hashes

    def get_pane_alert(self, pane_id: str) -> tuple[str, float, str] | None:
        return self._pane_alert_hashes.get(pane_id)

    def set_pane_alert(
        self, pane_id: str, content_hash: str, timestamp: float, window_id: str
    ) -> None:
        self._pane_alert_hashes[pane_id] = (content_hash, timestamp, window_id)

    def remove_pane_alert(self, pane_id: str) -> None:
        self._pane_alert_hashes.pop(pane_id, None)

    def prune_stale_pane_alerts(self, window_id: str, live_pane_ids: set[str]) -> None:
        stale = [
            pid
            for pid, v in self._pane_alert_hashes.items()
            if v[2] == window_id and pid not in live_pane_ids
        ]
        for pid in stale:
            self._pane_alert_hashes.pop(pid, None)

    def clear_pane_alerts(self, window_id: str) -> None:
        stale = [pid for pid, v in self._pane_alert_hashes.items() if v[2] == window_id]
        for pid in stale:
            self._pane_alert_hashes.pop(pid, None)

    def clear_all_alerts(self) -> None:
        self._pane_alert_hashes.clear()


class TopicLifecycleStrategy:
    def __init__(self, poll_state: TerminalPollState) -> None:
        self._poll_state = poll_state
        self._states: dict[tuple[int, int], TopicPollState] = {}
        self._dead_notified: set[tuple[int, int, str]] = set()
        topic_state.register_bound("topic", self.clear_state)
        topic_state.register_bound("topic", self.clear_dead_notification)

    def get_state(self, user_id: int, thread_id: int) -> TopicPollState:
        return self._states.setdefault((user_id, thread_id), TopicPollState())

    def is_dead_notified(self, user_id: int, thread_id: int, window_id: str) -> bool:
        return (user_id, thread_id, window_id) in self._dead_notified

    def mark_dead_notified(self, user_id: int, thread_id: int, window_id: str) -> None:
        self._dead_notified.add((user_id, thread_id, window_id))

    def iter_topic_states(self) -> list[tuple[int, int, TopicPollState]]:
        return [(uid, tid, ts) for (uid, tid), ts in self._states.items()]

    def clear_state(self, user_id: int, thread_id: int) -> None:
        self._states.pop((user_id, thread_id), None)

    def start_autoclose_timer(
        self, user_id: int, thread_id: int, state: str, now: float
    ) -> None:
        ts = self.get_state(user_id, thread_id)
        existing = ts.autoclose
        if existing is None or existing[0] != state:
            ts.autoclose = (state, now)

    def clear_autoclose_timer(self, user_id: int, thread_id: int) -> None:
        ts = self._states.get((user_id, thread_id))
        if ts:
            ts.autoclose = None

    def reset_autoclose_state(self) -> None:
        for ts in self._states.values():
            ts.autoclose = None
        self._poll_state.reset_all_unbound_timers()

    def clear_dead_notification(self, user_id: int, thread_id: int) -> None:
        self._dead_notified.difference_update(
            {k for k in self._dead_notified if k[0] == user_id and k[1] == thread_id}
        )

    def reset_dead_notification_state(self) -> None:
        self._dead_notified.clear()

    def clear_probe_failures(self, window_id: str) -> None:
        self._poll_state.reset_probe_failures(window_id)

    def clear_typing_state(self, user_id: int, thread_id: int) -> None:
        ts = self._states.get((user_id, thread_id))
        if ts:
            ts.last_typing_sent = None

    def reset_typing_state(self) -> None:
        for ts in self._states.values():
            ts.last_typing_sent = None

    def record_typing_sent(self, user_id: int, thread_id: int) -> None:
        self.get_state(user_id, thread_id).last_typing_sent = time.monotonic()

    def is_typing_throttled(self, user_id: int, thread_id: int) -> bool:
        ts = self._states.get((user_id, thread_id))
        if not ts or ts.last_typing_sent is None:
            return False
        return (time.monotonic() - ts.last_typing_sent) < TYPING_INTERVAL

    def should_skip_probe(self, window_id: str) -> bool:
        ws = self._poll_state.get_state(window_id)
        return ws.probe_failures >= MAX_PROBE_FAILURES

    def record_probe_failure(self, window_id: str) -> int:
        ws = self._poll_state.get_state(window_id)
        ws.probe_failures += 1
        count = ws.probe_failures
        if count == MAX_PROBE_FAILURES:
            logger.info(
                "Suspending topic probe for %s after %d consecutive failures",
                window_id,
                count,
            )
        return count


class PaneStatusStrategy:
    PANE_FORWARD_MIN_INTERVAL = 5.0

    def __init__(
        self,
        screen_buffer: TerminalScreenBuffer,
        interactive: InteractiveUIStrategy,
    ) -> None:
        self._screen_buffer = screen_buffer
        self._interactive = interactive
        self._pane_content_hash: dict[str, int] = {}
        self._pane_forward_ts: dict[str, float] = {}
        self._scanned_windows: set[str] = set()
        topic_state.register_bound("window", self._clear_pane_content_state)

    def _clear_pane_content_state(self, window_id: str) -> None:
        from ...window_state_ports.pane_state import list_pane_projections

        for projection in list_pane_projections(window_id):
            self._pane_content_hash.pop(projection.pane_id, None)
            self._pane_forward_ts.pop(projection.pane_id, None)
        self._scanned_windows.discard(window_id)

    def has_scanned_window(self, window_id: str) -> bool:
        return window_id in self._scanned_windows

    @staticmethod
    def classify_pane(active: bool, status: StatusUpdate | None) -> PaneStateName:
        if status is not None and status.is_interactive:
            return "blocked"
        if active:
            return "active"
        return "idle"

    def reconcile_dead_panes(
        self, window_id: str, live_pane_ids: set[str]
    ) -> list[tuple[str, str | None]]:
        from ...window_state_ports.pane_state import (
            list_pane_projections,
            remove_pane,
        )

        gone = [
            (p.pane_id, p.name)
            for p in list_pane_projections(window_id)
            if p.pane_id not in live_pane_ids
        ]
        for pid, _ in gone:
            remove_pane(window_id, pid)
            self._interactive.remove_pane_alert(pid)
            self._pane_content_hash.pop(pid, None)
        return gone

    def record_pane_state(
        self,
        window_id: str,
        pane_id: str,
        new_state: PaneStateName,
        *,
        provider: str = "",
        last_active_ts: float | None = None,
    ) -> PaneStateName | None:
        from ...window_state_ports.pane_state import (
            get_pane_projection,
            upsert_pane,
        )

        existing = get_pane_projection(window_id, pane_id)
        prev_state = existing.state if existing else None
        upsert_pane(
            window_id,
            pane_id,
            provider=provider or None,
            last_active_ts=last_active_ts,
            state=new_state,
        )
        return prev_state

    def _resolve_pane_provider(
        self, window_id: str, pane_command: str, fallback: str
    ) -> str:
        from ...providers import detect_provider_from_command

        from ...window_query import get_window_provider

        return (
            detect_provider_from_command(pane_command)
            or get_window_provider(window_id)
            or fallback
        )

    def _track(
        self,
        transitions: list[PaneTransition],
        pane_id: str,
        prev: PaneStateName | None,
        new: PaneStateName,
    ) -> None:
        if prev != new:
            transitions.append(
                PaneTransition(pane_id=pane_id, prev_state=prev, new_state=new)
            )

    async def _classify_non_active(
        self, window_id: str, pane: TmuxPaneInfo, provider: AgentProvider
    ) -> tuple[PaneStateName, StatusUpdate | None, str]:
        from ...multiplexer import multiplexer as tmux_manager

        pane_text = await tmux_manager.capture_pane_by_id(
            pane.pane_id, window_id=window_id
        )
        if not pane_text:
            return "idle", None, ""
        status = provider.parse_terminal_status(pane_text, pane_title="")
        return self.classify_pane(pane.active, status), status, pane_text

    async def _maybe_surface_alert(
        self,
        bot: Bot,
        user_id: int,
        window_id: str,
        thread_id: int,
        pane_id: str,
        prompt_text: str,
        now_mono: float,
        on_blocked: BlockedAlertCallback,
    ) -> None:
        existing = self._interactive.get_pane_alert(pane_id)
        if existing and existing[0] == prompt_text:
            return
        self._interactive.set_pane_alert(pane_id, prompt_text, now_mono, window_id)
        logger.info(
            "Pane %s in window %s has interactive UI, surfacing alert",
            pane_id,
            window_id,
        )
        await on_blocked(bot, user_id, window_id, thread_id, pane_id)

    async def scan_window(
        self,
        bot: Bot,
        user_id: int,
        window_id: str,
        thread_id: int,
        *,
        on_blocked: BlockedAlertCallback,
        on_pane_output: PaneOutputCallback | None = None,
    ) -> list[PaneTransition]:
        from ...providers import get_provider_for_window

        from ...multiplexer import multiplexer as tmux_manager

        from ...window_query import get_window_provider

        if self._screen_buffer.is_single_pane_cached(window_id):
            return []

        is_first_scan = window_id not in self._scanned_windows

        panes = await tmux_manager.list_panes(window_id)
        self._screen_buffer.update_pane_count_cache(window_id, len(panes))
        live_pane_ids = {p.pane_id for p in panes}
        self._interactive.prune_stale_pane_alerts(window_id, live_pane_ids)

        transitions: list[PaneTransition] = []
        for gone_pid, gone_name in self.reconcile_dead_panes(window_id, live_pane_ids):
            transitions.append(
                PaneTransition(
                    pane_id=gone_pid,
                    prev_state=None,
                    new_state="dead",
                    name=gone_name,
                )
            )

        if len(panes) <= 1:
            self._record_single_pane(window_id, panes, transitions)
            self._scanned_windows.add(window_id)
            if is_first_scan:
                transitions[:] = [
                    t
                    for t in transitions
                    if t.new_state == "dead" or t.prev_state is not None
                ]
            return transitions

        now_mono = time.monotonic()
        now_wall = time.time()
        window_provider = get_provider_for_window(
            window_id, provider_name=get_window_provider(window_id)
        )

        for pane in panes:
            pane_provider = self._resolve_pane_provider(
                window_id, pane.command, window_provider.capabilities.name
            )
            await self._scan_one_pane(
                bot,
                user_id,
                thread_id,
                window_id,
                pane,
                pane_provider,
                window_provider,
                now_mono,
                now_wall,
                transitions,
                on_blocked,
                on_pane_output,
            )
        self._scanned_windows.add(window_id)
        if is_first_scan:
            transitions[:] = [
                t
                for t in transitions
                if t.new_state == "dead" or t.prev_state is not None
            ]
        return transitions

    def _record_single_pane(
        self,
        window_id: str,
        panes: list[TmuxPaneInfo],
        transitions: list[PaneTransition],
    ) -> None:
        for pane in panes:
            pane_provider = self._resolve_pane_provider(window_id, pane.command, "")
            new_state: PaneStateName = "active" if pane.active else "idle"
            prev = self.record_pane_state(
                window_id,
                pane.pane_id,
                new_state,
                provider=pane_provider,
                last_active_ts=time.time() if pane.active else None,
            )
            self._track(transitions, pane.pane_id, prev, new_state)

    async def _scan_one_pane(
        self,
        bot: Bot,
        user_id: int,
        thread_id: int,
        window_id: str,
        pane: TmuxPaneInfo,
        pane_provider: str,
        window_provider: AgentProvider,
        now_mono: float,
        now_wall: float,
        transitions: list[PaneTransition],
        on_blocked: BlockedAlertCallback,
        on_pane_output: PaneOutputCallback | None = None,
    ) -> None:
        if pane.active:
            prev = self.record_pane_state(
                window_id,
                pane.pane_id,
                "active",
                provider=pane_provider,
                last_active_ts=now_wall,
            )
            self._track(transitions, pane.pane_id, prev, "active")
            return

        new_state, status, pane_text = await self._classify_non_active(
            window_id, pane, window_provider
        )
        prev = self.record_pane_state(
            window_id, pane.pane_id, new_state, provider=pane_provider
        )
        self._track(transitions, pane.pane_id, prev, new_state)

        if pane_text and on_pane_output is not None:
            await self._maybe_forward_subscribed(
                bot,
                user_id,
                window_id,
                thread_id,
                pane.pane_id,
                pane_text,
                on_pane_output,
            )

        if new_state != "blocked":
            self._interactive.remove_pane_alert(pane.pane_id)
            return

        prompt_text = (status.raw_text if status else "") or ""
        await self._maybe_surface_alert(
            bot,
            user_id,
            window_id,
            thread_id,
            pane.pane_id,
            prompt_text,
            now_mono,
            on_blocked,
        )

    async def _maybe_forward_subscribed(
        self,
        bot: Bot,
        user_id: int,
        window_id: str,
        thread_id: int,
        pane_id: str,
        pane_text: str,
        on_pane_output: PaneOutputCallback,
    ) -> None:
        from ...window_state_ports.pane_state import get_pane_projection

        pane = get_pane_projection(window_id, pane_id)
        if pane is None or not pane.subscribed:
            self._pane_content_hash.pop(pane_id, None)
            self._pane_forward_ts.pop(pane_id, None)
            return
        content_hash = zlib.crc32(pane_text.encode("utf-8", errors="replace"))
        if self._pane_content_hash.get(pane_id) == content_hash:
            return
        now = time.monotonic()
        last_forward = self._pane_forward_ts.get(pane_id)
        if (
            last_forward is not None
            and now - last_forward < self.PANE_FORWARD_MIN_INTERVAL
        ):
            return
        self._pane_content_hash[pane_id] = content_hash
        self._pane_forward_ts[pane_id] = now
        await on_pane_output(bot, user_id, window_id, thread_id, pane_id, pane_text)


terminal_poll_state = TerminalPollState()
terminal_screen_buffer = TerminalScreenBuffer(terminal_poll_state)
interactive_strategy = InteractiveUIStrategy()
lifecycle_strategy = TopicLifecycleStrategy(terminal_poll_state)
pane_status_strategy = PaneStatusStrategy(terminal_screen_buffer, interactive_strategy)


def reset_window_polling_state(window_id: str) -> None:
    terminal_poll_state.clear_seen_status(window_id)
    terminal_screen_buffer.clear_screen_buffer(window_id)
