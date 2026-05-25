"""Session monitoring service — thin coordinator and poll loop.

Orchestrates the session-monitoring subsystem:
  1. Reads hook events via event_reader and dispatches them.
  2. Reconciles session_map changes via SessionLifecycle.
  3. Reads transcript updates via TranscriptReader.
  4. Emits NewMessage / NewWindowEvent to registered callbacks.

All heavy logic lives in the extracted modules:
  - event_reader.py   — reads events.jsonl incrementally
  - idle_tracker.py   — per-session idle timers
  - session_lifecycle.py — session-map diff, claude_task_state authority
  - transcript_reader.py — transcript I/O and parsing

Key classes: SessionMonitor, NewMessage, NewWindowEvent, SessionInfo.
Re-exported from transcript_reader for backward-compatible imports.
"""

import asyncio
import time
import structlog
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from telegram.error import TelegramError

from .config import config
from .event_reader import read_new_events
from .idle_tracker import IdleTracker
from .monitor_state import MonitorState
from .providers import get_provider_for_window, registry  # noqa: F401 (used by test patches)
from .session_map import parse_session_map, read_session_map_raw
from .session_lifecycle import session_lifecycle
from .tmux_manager import tmux_manager
from .monitor_events import NewMessage, NewWindowEvent, SessionInfo
from .transcript_reader import TranscriptReader
from .utils import atomic_write_json, task_done_callback

import json

# Re-export for backward-compatible imports from other modules
__all__ = [
    "NewMessage",
    "NewWindowEvent",
    "SessionInfo",
    "SessionMonitor",
    "get_active_monitor",
    "set_active_monitor",
]

_CallbackError = Exception
_LoopError = (OSError, RuntimeError, json.JSONDecodeError, ValueError, TelegramError)

_BACKOFF_MIN = 2.0
_BACKOFF_MAX = 30.0
_MSG_PREVIEW_LENGTH = 80
_MONITOR_LOOP_WARN_SECS = 1.0
_TRANSCRIPT_ACTIVE_SECS = 30

logger = structlog.get_logger()


class SessionMonitor:
    """Monitors Claude Code sessions for new assistant messages.

    Thin coordinator: delegates I/O to TranscriptReader, event reading to
    event_reader, session-map diffing to SessionLifecycle, and idle tracking
    to IdleTracker.
    """

    def __init__(
        self,
        projects_path: Path | None = None,
        poll_interval: float | None = None,
        state_file: Path | None = None,
    ):
        self.projects_path = (
            projects_path if projects_path is not None else config.claude_projects_path
        )
        self.poll_interval = (
            poll_interval if poll_interval is not None else config.monitor_poll_interval
        )

        self.state = MonitorState(state_file=state_file or config.monitor_state_file)
        self.state.load()

        self._running = False
        self._task: asyncio.Task | None = None
        self._message_callback: Callable[[NewMessage], Awaitable[None]] | None = None
        self._new_window_callback: (
            Callable[[NewWindowEvent], Awaitable[None]] | None
        ) = None
        # Lazy: providers.base imports HookEvent and gets imported back
        # through tmux_manager → providers; keep at call site.
        # Lazy: HookEvent pulled by hook dispatch path; defer until that path runs
        from .providers.base import HookEvent

        self._hook_event_callback: Callable[[HookEvent], Awaitable[None]] | None = None

        self._idle_tracker = IdleTracker()
        self._transcript_reader = TranscriptReader(self.state, self._idle_tracker)
        self._emitted_new_window_ids: set[str] = set()

    # Delegation properties for backward-compatible test access
    @property
    def _last_session_map(self) -> dict:
        return session_lifecycle.last_session_map

    @_last_session_map.setter
    def _last_session_map(self, value: dict) -> None:
        session_lifecycle.initialize(value)

    @property
    def _last_activity(self) -> dict:
        return self._idle_tracker._last_activity

    @property
    def _file_mtimes(self) -> dict:
        return self._transcript_reader._file_mtimes

    @property
    def _pending_tools(self) -> dict:
        return self._transcript_reader._pending_tools

    def get_last_activity(self, session_id: str) -> float | None:
        """Get monotonic timestamp of last transcript activity for a session."""
        return self._idle_tracker.get_last_activity(session_id)

    def set_message_callback(
        self, callback: Callable[[NewMessage], Awaitable[None]]
    ) -> None:
        self._message_callback = callback

    def set_new_window_callback(
        self, callback: Callable[[NewWindowEvent], Awaitable[None]]
    ) -> None:
        self._new_window_callback = callback

    def set_hook_event_callback(self, callback: Callable[..., Awaitable[None]]) -> None:
        self._hook_event_callback = callback

    def record_hook_activity(self, window_id: str) -> None:
        """Record hook-based activity for a window (resets idle timers)."""
        session_id = session_lifecycle.resolve_session_id(window_id)
        if session_id:
            self._idle_tracker.record_activity(session_id)

    @staticmethod
    def _transcript_mtime(transcript_path: str) -> float | None:
        if not transcript_path:
            return None
        try:
            return Path(transcript_path).stat().st_mtime
        except OSError:
            return None

    def _adopt_newer_discovered_transcripts(
        self, raw_session_map: dict[str, Any] | None
    ) -> tuple[dict[str, Any] | None, bool]:
        """Replace stale hook-backed transcript entries with newer discovered files.

        Hook-backed providers normally keep ``session_map.json`` current. When a
        hook runner is missing or crashes, a tmux pane can continue in a new
        provider session while ccgram reads an old, still-existing transcript.
        If the recorded transcript is no longer active and the provider can
        discover a newer transcript for the same cwd, adopt it as the current
        session so message polling resumes.
        """
        if not raw_session_map:
            return raw_session_map, False

        prefix = f"{config.tmux_session_name}:"
        current_map = parse_session_map(raw_session_map, prefix)
        now = time.time()
        changed = False

        for window_id, details in current_map.items():
            if not window_id.startswith("@"):
                continue

            current_path = details.get("transcript_path", "")
            current_mtime = self._transcript_mtime(current_path)
            if (
                current_mtime is not None
                and now - current_mtime < _TRANSCRIPT_ACTIVE_SECS
            ):
                continue

            provider = get_provider_for_window(
                window_id, provider_name=details.get("provider_name", "")
            )
            if not provider.capabilities.supports_hook:
                continue

            discovered = provider.discover_transcript(
                details.get("cwd", ""), f"{config.tmux_session_name}:{window_id}"
            )
            if discovered is None:
                continue
            if (
                discovered.session_id == details.get("session_id")
                and discovered.transcript_path == current_path
            ):
                continue

            discovered_mtime = self._transcript_mtime(discovered.transcript_path)
            if current_mtime is not None and (
                discovered_mtime is None or discovered_mtime <= current_mtime
            ):
                continue

            raw_key = f"{config.tmux_session_name}:{window_id}"
            existing = raw_session_map.get(raw_key, {})
            if not isinstance(existing, dict):
                existing = {}
            raw_session_map[raw_key] = {
                "session_id": discovered.session_id,
                "cwd": discovered.cwd,
                "window_name": existing.get(
                    "window_name", details.get("window_name", "")
                ),
                "transcript_path": discovered.transcript_path,
                "provider_name": existing.get(
                    "provider_name",
                    details.get("provider_name", provider.capabilities.name),
                ),
            }
            logger.info(
                "Adopted newer discovered transcript for %s: %s -> %s",
                window_id,
                details.get("session_id", "")[:8],
                discovered.session_id[:8],
            )
            changed = True

        if changed:
            atomic_write_json(config.session_map_file, raw_session_map)
        return raw_session_map, changed

    async def check_for_updates(self, current_map: dict) -> list[NewMessage]:
        """Check all sessions for new assistant messages.

        Routes sessions to _process_session_file (allowing test spying) and
        delegates the actual I/O to TranscriptReader. Uses _get_active_cwds()
        for fallback session discovery so tests can stub tmux calls.
        """
        new_messages: list[NewMessage] = []
        sid_to_wid = {v["session_id"]: wid for wid, v in current_map.items()}

        direct_sessions: list[tuple[str, Path]] = []
        fallback_session_ids: set[str] = set()

        for details in current_map.values():
            session_id = details["session_id"]
            transcript_path = details.get("transcript_path", "")
            if transcript_path:
                path = Path(transcript_path)
                if path.exists():
                    direct_sessions.append((session_id, path))
                    continue
            fallback_session_ids.add(session_id)

        for session_id, file_path in direct_sessions:
            try:
                await self._process_session_file(
                    session_id,
                    file_path,
                    new_messages,
                    window_id=sid_to_wid.get(session_id, ""),
                )
            except Exception:
                logger.exception("Error processing session %s", session_id)

        if fallback_session_ids:
            active_cwds = await self._get_active_cwds()
            sessions = self._scan_projects_sync(active_cwds) if active_cwds else []
            for session_info in sessions:
                if session_info.session_id not in fallback_session_ids:
                    continue
                try:
                    await self._process_session_file(
                        session_info.session_id,
                        session_info.file_path,
                        new_messages,
                        window_id=sid_to_wid.get(session_info.session_id, ""),
                    )
                except Exception:
                    logger.exception(
                        "Error processing session %s", session_info.session_id
                    )

        self.state.save_if_dirty()
        return new_messages

    async def _process_session_file(
        self, session_id: str, file_path: Path, new_messages: list, window_id: str = ""
    ) -> None:
        """Process a single session file (delegates to TranscriptReader)."""
        await self._transcript_reader._process_session_file(
            session_id, file_path, new_messages, window_id=window_id
        )

    def _scan_projects_sync(self, active_cwds: set) -> list:
        """Scan projects synchronously (delegates to TranscriptReader)."""
        return self._transcript_reader._scan_projects_sync(
            self.projects_path, active_cwds
        )

    async def _get_active_cwds(self) -> set[str]:
        """Get normalized cwds of all active tmux windows (delegates to TranscriptReader)."""
        return await self._transcript_reader._get_active_cwds()

    async def _read_new_lines(
        self, session: Any, file_path: Path, window_id: str = ""
    ) -> list:
        """Read new lines from session file (delegates to TranscriptReader)."""
        return await self._transcript_reader._read_new_lines(
            session, file_path, window_id
        )

    async def _read_hook_events(self) -> None:
        """Read new lines from events.jsonl, dispatch callbacks, and reconcile map."""
        if not self._hook_event_callback:
            return

        offset_before = self.state.events_offset
        events, new_offset = await read_new_events(
            config.events_file, self.state.events_offset
        )
        self.state.events_offset = new_offset
        if new_offset != offset_before:
            self.state._dirty = True

        session_starts: dict[str, dict[str, str]] = {}
        for event in events:
            if event.event_type == "SessionStart" and event.window_key:
                session_starts[event.window_key] = {
                    "session_id": event.session_id,
                    **event.data,
                }
            try:
                await self._hook_event_callback(event)
            except _CallbackError:
                logger.exception("Hook event callback error for %s", event.event_type)

        if session_starts:
            await self._reconcile_session_map(session_starts)

    async def _reconcile_session_map(
        self, session_starts: dict[str, dict[str, str]]
    ) -> None:
        """Fix session_map entries when a stale SessionStart was rejected."""
        raw = await read_session_map_raw()
        if not raw:
            return

        changed = False
        for window_key, start_data in session_starts.items():
            existing = raw.get(window_key)
            if not isinstance(existing, dict) or existing.get(
                "session_id"
            ) == start_data.get("session_id"):
                continue

            # Check if the existing transcript is still actively written to
            # (mtime within 30s). A stale transcript that merely exists on
            # disk should not block reconciliation — the new session's
            # transcript may have been created after the hook fired.
            existing_tp = existing.get("transcript_path", "")
            new_tp = start_data.get("transcript_path", "")
            if existing_tp:
                try:
                    existing_mtime = Path(existing_tp).stat().st_mtime
                    if time.time() - existing_mtime < _TRANSCRIPT_ACTIVE_SECS:
                        continue  # existing transcript still active
                except OSError:
                    pass  # existing transcript gone
            if not new_tp or not Path(new_tp).exists():
                continue

            logger.info(
                "Reconciling session_map for %s: %s -> %s",
                window_key,
                existing.get("session_id", "?")[:8],
                start_data.get("session_id", "?")[:8],
            )
            raw[window_key] = {
                "session_id": start_data.get("session_id", ""),
                "cwd": start_data.get("cwd", ""),
                "window_name": start_data.get("window_name", ""),
                "transcript_path": new_tp,
                "provider_name": existing.get("provider_name", "claude"),
            }
            changed = True

        if changed:
            # Lazy: utils imports app-wide helpers; only needed for rare reconciliation.
            from .utils import atomic_write_json

            atomic_write_json(config.session_map_file, raw)

    async def _load_current_session_map(
        self, raw: dict[str, Any] | None = None
    ) -> dict[str, dict[str, str]]:
        """Load current session_map and return window_key -> details mapping.

        If ``raw`` is provided (already read by the caller), parse it directly
        to avoid a redundant file read.  Otherwise read session_map.json.
        """
        if raw is None:
            raw = await read_session_map_raw()
        if not raw:
            return {}
        prefix = f"{config.tmux_session_name}:"
        return parse_session_map(raw, prefix)

    async def _cleanup_all_stale_sessions(self) -> None:
        """Clean up all tracked sessions not in current session_map (startup)."""
        current_map = await self._load_current_session_map()
        active_session_ids = {v["session_id"] for v in current_map.values()}

        stale_sessions = [
            sid for sid in self.state.tracked_sessions if sid not in active_session_ids
        ]
        if stale_sessions:
            logger.info(
                "[Startup cleanup] Removing %d stale sessions", len(stale_sessions)
            )
            for session_id in stale_sessions:
                self._transcript_reader.clear_session(session_id)
                self._idle_tracker.clear_session(session_id)
            self.state.save_if_dirty()

    async def _detect_and_cleanup_changes(
        self, raw: dict | None = None
    ) -> dict[str, dict[str, str]]:
        """Reconcile session_map; clean up replaced/removed sessions; fire new-window events."""
        raw, adopted_discovery = self._adopt_newer_discovered_transcripts(raw)
        if adopted_discovery:
            try:
                from .session_map import session_map_sync

                await session_map_sync.load_session_map(raw)
            except RuntimeError as exc:
                if "SessionManager" not in str(exc):
                    raise
        current_map = await self._load_current_session_map(raw)
        result = session_lifecycle.reconcile(current_map, self._idle_tracker)

        for session_id in result.sessions_to_remove:
            self._transcript_reader.clear_session(session_id)
        if result.sessions_to_remove:
            self.state.save_if_dirty()

        for details in result.new_windows.values():
            self._transcript_reader.mark_catch_up(details["session_id"])
        for details in result.changed_windows.values():
            self._transcript_reader.mark_catch_up(details["session_id"])

        adoption_windows = dict(result.new_windows)
        if result.changed_windows and self._new_window_callback:
            # Lazy: thread_router is wired into session_manager which imports
            # session_monitor; hoisting forms a startup cycle.
            # Lazy: proxies wired by SessionManager constructor
            from .thread_router import thread_router

            for window_id, details in result.changed_windows.items():
                if not thread_router.has_window(window_id):
                    adoption_windows[window_id] = details

        if adoption_windows:
            # Lazy: session.py imports session_monitor at top; hoisting
            # session_manager forms a hard cycle on bootstrap.
            from .session import session_manager as _sm

            for window_id, details in adoption_windows.items():
                provider_name = details.get("provider_name", "")
                if provider_name:
                    _sm.set_window_provider(window_id, provider_name)

                if self._new_window_callback:
                    event = NewWindowEvent(
                        window_id=window_id,
                        session_id=details["session_id"],
                        window_name=details.get("window_name", ""),
                        cwd=details.get("cwd", ""),
                    )
                    try:
                        await self._new_window_callback(event)
                    except _CallbackError:
                        logger.exception(
                            "New window callback error (session_map path) for %s",
                            window_id,
                        )

        return result.current_map

    async def _emit_unbound_window_events(
        self, current_map: dict[str, dict[str, str]], session_map_sync: Any
    ) -> None:
        """Emit NewWindowEvent once for live windows not represented in session_map."""
        all_windows = await tmux_manager.list_windows()
        external_windows = await tmux_manager.discover_external_sessions()
        all_windows = all_windows + external_windows
        live_window_ids = {w.window_id for w in all_windows}
        session_map_sync.prune_session_map(live_window_ids)
        self._emitted_new_window_ids &= live_window_ids
        known_window_ids = set(current_map.keys())

        for window in all_windows:
            if window.window_id in known_window_ids:
                continue
            if window.window_id in self._emitted_new_window_ids:
                continue
            # Lazy: same cycle as the earlier thread_router import.
            from .thread_router import thread_router

            already_bound = any(
                wid == window.window_id
                for _, _, wid in thread_router.iter_thread_bindings()
            )
            if already_bound or not self._new_window_callback:
                continue

            self._emitted_new_window_ids.add(window.window_id)
            event = NewWindowEvent(
                window_id=window.window_id,
                session_id="",
                window_name=window.window_name,
                cwd=window.cwd,
            )
            try:
                await self._new_window_callback(event)
            except _CallbackError:
                logger.exception("New window callback error for %s", window.window_id)

    async def _monitor_loop(self) -> None:
        """Background poll loop."""
        logger.info("Session monitor started, polling every %ss", self.poll_interval)

        # Lazy: session_map imports session_monitor types via shared
        # state cycle; keep at call site.
        # Lazy: proxies wired by SessionManager constructor
        from .session_map import session_map_sync

        await self._cleanup_all_stale_sessions()
        initial_map = await self._load_current_session_map()
        session_lifecycle.initialize(initial_map)

        error_streak = 0
        while self._running:
            try:
                loop_started_at = time.monotonic()
                await self._read_hook_events()
                raw_session_map = await read_session_map_raw()
                await session_map_sync.load_session_map(raw_session_map)

                current_map = await self._detect_and_cleanup_changes(raw_session_map)

                await self._emit_unbound_window_events(current_map, session_map_sync)

                new_messages = await self.check_for_updates(current_map)

                for msg in new_messages:
                    structlog.contextvars.clear_contextvars()
                    structlog.contextvars.bind_contextvars(session_id=msg.session_id)
                    status = "complete" if msg.is_complete else "streaming"
                    preview = msg.text[:_MSG_PREVIEW_LENGTH] + (
                        "..." if len(msg.text) > _MSG_PREVIEW_LENGTH else ""
                    )
                    logger.debug("[%s] session=%s: %s", status, msg.session_id, preview)
                    if self._message_callback:
                        try:
                            await self._message_callback(msg)
                        except _CallbackError:
                            logger.exception(
                                "Message callback error for session=%s",
                                msg.session_id,
                            )

                loop_elapsed_secs = time.monotonic() - loop_started_at
                if config.diagnostic_logs and (
                    loop_elapsed_secs >= _MONITOR_LOOP_WARN_SECS
                    or len(new_messages) > 0
                ):
                    logger.warning(
                        "monitor_loop_stats",
                        tracked_sessions=len(current_map),
                        emitted_messages=len(new_messages),
                        elapsed_ms=int(loop_elapsed_secs * 1000),
                    )

            except _LoopError:
                logger.exception("Monitor loop error")
                backoff_delay = min(_BACKOFF_MAX, _BACKOFF_MIN * (2**error_streak))
                error_streak += 1
                await asyncio.sleep(backoff_delay)
                continue
            except Exception:
                logger.exception("Unexpected error in monitor loop")
                backoff_delay = min(_BACKOFF_MAX, _BACKOFF_MIN * (2**error_streak))
                error_streak += 1
                await asyncio.sleep(backoff_delay)
                continue

            error_streak = 0
            await asyncio.sleep(self.poll_interval)

        logger.info("Session monitor stopped")

    def start(self) -> None:
        if self._running:
            logger.debug("Monitor already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        self._task.add_done_callback(task_done_callback)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self.state.save()
        # Distinct from the loop's "Session monitor stopped" (logged when the
        # poll loop actually exits) — this marks the stop request + state save.
        logger.info("Session monitor stop requested; state saved")


_active_monitor: SessionMonitor | None = None


def set_active_monitor(monitor: SessionMonitor) -> None:
    """Set the active SessionMonitor instance (called by bot.py post_init)."""
    global _active_monitor  # noqa: PLW0603
    _active_monitor = monitor


def clear_active_monitor() -> None:
    """Clear the active SessionMonitor singleton (shutdown / test reset)."""
    global _active_monitor  # noqa: PLW0603
    _active_monitor = None


def get_active_monitor() -> SessionMonitor | None:
    """Return the active SessionMonitor instance."""
    return _active_monitor
