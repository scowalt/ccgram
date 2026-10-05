from __future__ import annotations

import structlog
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Self, cast

logger = structlog.get_logger()

APPROVAL_MODES: frozenset[str] = frozenset({"normal", "yolo"})
DEFAULT_APPROVAL_MODE = "normal"
BATCH_MODES: frozenset[str] = frozenset({"batched", "ephemeral", "verbose"})
DEFAULT_BATCH_MODE = "ephemeral"
_BATCH_CYCLE: dict[str, str] = {
    "batched": "ephemeral",
    "ephemeral": "verbose",
    "verbose": "batched",
}

TOOL_CALL_VISIBILITY_MODES: tuple[str, ...] = ("default", "shown", "hidden")
DEFAULT_TOOL_CALL_VISIBILITY: str = "default"

WINDOW_ORIGINS: frozenset[str] = frozenset({"manual_discovered", "ccgram_created"})
DEFAULT_WINDOW_ORIGIN = "manual_discovered"
CCGRAM_CREATED_WINDOW_ORIGIN = "ccgram_created"

PaneState = Literal["active", "idle", "blocked", "dead"]
PANE_STATES: frozenset[str] = frozenset({"active", "idle", "blocked", "dead"})
DEFAULT_PANE_STATE: PaneState = "idle"


class _Sentinel:
    __slots__ = ()


_SENTINEL = _Sentinel()


@dataclass
class PaneInfo:
    pane_id: str
    name: str | None = None
    provider: str = ""
    last_active_ts: float = 0.0
    state: PaneState = DEFAULT_PANE_STATE
    subscribed: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"pane_id": self.pane_id}
        if self.name is not None:
            d["name"] = self.name
        if self.provider:
            d["provider"] = self.provider
        if self.last_active_ts:
            d["last_active_ts"] = self.last_active_ts
        if self.state != DEFAULT_PANE_STATE:
            d["state"] = self.state
        if self.subscribed:
            d["subscribed"] = True
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        raw_state = data.get("state", DEFAULT_PANE_STATE)
        state: PaneState = raw_state if raw_state in PANE_STATES else DEFAULT_PANE_STATE
        return cls(
            pane_id=data.get("pane_id", ""),
            name=data.get("name"),
            provider=data.get("provider", ""),
            last_active_ts=float(data.get("last_active_ts", 0.0) or 0.0),
            state=state,
            subscribed=bool(data.get("subscribed", False)),
        )


@dataclass
class WindowState:
    session_id: str = ""
    cwd: str = ""
    window_name: str = ""
    transcript_path: str = ""
    provider_name: str = ""
    approval_mode: str = DEFAULT_APPROVAL_MODE
    batch_mode: str = DEFAULT_BATCH_MODE
    tool_call_visibility: str = DEFAULT_TOOL_CALL_VISIBILITY
    origin: str = DEFAULT_WINDOW_ORIGIN
    panes: dict[str, PaneInfo] = field(default_factory=dict)
    pane_lifecycle_notify: bool | None = None
    rc_probe_state: Literal["armed", "classified"] | None = None
    rc_armed_at: float | None = None
    worktree_path: str | None = None
    worktree_branch: str | None = None
    provider_manual_override: bool = False

    def to_dict(self) -> dict[str, Any]:  # noqa: C901
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "cwd": self.cwd,
        }
        if self.window_name:
            d["window_name"] = self.window_name
        if self.transcript_path:
            d["transcript_path"] = self.transcript_path
        if self.provider_name:
            d["provider_name"] = self.provider_name
        if self.approval_mode != DEFAULT_APPROVAL_MODE:
            d["approval_mode"] = self.approval_mode
        if self.batch_mode != DEFAULT_BATCH_MODE:
            d["batch_mode"] = self.batch_mode
        if self.tool_call_visibility != DEFAULT_TOOL_CALL_VISIBILITY:
            d["tool_call_visibility"] = self.tool_call_visibility
        if self.origin != DEFAULT_WINDOW_ORIGIN:
            d["origin"] = self.origin
        if self.panes:
            d["panes"] = {pid: p.to_dict() for pid, p in self.panes.items()}
        if self.pane_lifecycle_notify is not None:
            d["pane_lifecycle_notify"] = self.pane_lifecycle_notify
        if self.worktree_path:
            d["worktree_path"] = self.worktree_path
        if self.worktree_branch:
            d["worktree_branch"] = self.worktree_branch
        if self.provider_manual_override:
            d["provider_manual_override"] = True
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:  # noqa: C901
        raw_panes = data.get("panes") or {}
        panes: dict[str, PaneInfo] = {}
        if isinstance(raw_panes, dict):
            for pid, pdata in raw_panes.items():
                if not isinstance(pdata, dict):
                    continue
                pane = PaneInfo.from_dict(
                    {**pdata, "pane_id": pdata.get("pane_id", pid)}
                )
                panes[pid] = pane
        return cls(
            session_id=data.get("session_id", ""),
            cwd=data.get("cwd", ""),
            window_name=data.get("window_name", ""),
            transcript_path=data.get("transcript_path", ""),
            provider_name=data.get("provider_name", ""),
            approval_mode=data.get("approval_mode", DEFAULT_APPROVAL_MODE),
            batch_mode=data.get("batch_mode", DEFAULT_BATCH_MODE),
            tool_call_visibility=data.get(
                "tool_call_visibility", DEFAULT_TOOL_CALL_VISIBILITY
            ),
            origin=(
                data.get("origin", DEFAULT_WINDOW_ORIGIN)
                if data.get("origin", DEFAULT_WINDOW_ORIGIN) in WINDOW_ORIGINS
                else DEFAULT_WINDOW_ORIGIN
            ),
            panes=panes,
            pane_lifecycle_notify=data.get("pane_lifecycle_notify"),
            worktree_path=data.get("worktree_path"),
            worktree_branch=data.get("worktree_branch"),
            provider_manual_override=data.get("provider_manual_override", False),
        )


class WindowStateStore:
    def __init__(
        self,
        *,
        schedule_save: Callable[[], None],
        on_hookless_provider_switch: Callable[[str], None],
    ) -> None:
        self.window_states: dict[str, WindowState] = {}
        self._schedule_save: Callable[[], None] = schedule_save
        self._on_hookless_provider_switch: Callable[[str], None] = (
            on_hookless_provider_switch
        )

    def reset(self) -> None:
        self.window_states.clear()

    def to_dict(self) -> dict[str, Any]:
        return {k: v.to_dict() for k, v in self.window_states.items()}

    def from_dict(self, data: dict[str, Any]) -> None:
        self.window_states = {
            k: WindowState.from_dict(v) for k, v in data.items() if isinstance(v, dict)
        }

    def get_window_state(self, window_id: str) -> WindowState:
        if window_id not in self.window_states:
            self.window_states[window_id] = WindowState()
        return self.window_states[window_id]

    def update_cwd(self, window_id: str, cwd: str) -> None:
        if window_id in self.window_states:
            self.window_states[window_id].cwd = cwd
            self._schedule_save()

    def set_window_origin(self, window_id: str, origin: str) -> None:
        if origin not in WINDOW_ORIGINS:
            raise ValueError(f"Invalid window origin: {origin!r}")
        state = self.get_window_state(window_id)
        if state.origin == origin:
            return
        state.origin = origin
        self._schedule_save()

    def set_worktree(self, window_id: str, worktree_path: str, branch: str) -> None:
        state = self.get_window_state(window_id)
        if state.worktree_path == worktree_path and state.worktree_branch == branch:
            return
        state.worktree_path = worktree_path
        state.worktree_branch = branch
        self._schedule_save()

    def clear_worktree(self, window_id: str) -> None:
        state = self.window_states.get(window_id)
        if state is None:
            return
        if state.worktree_path is None and state.worktree_branch is None:
            return
        state.worktree_path = None
        state.worktree_branch = None
        self._schedule_save()

    def clear_session_fields(self, window_id: str) -> None:
        if window_id in self.window_states:
            self.window_states[window_id].session_id = ""
            self.window_states[window_id].cwd = ""
            self._schedule_save()

    def clear_window_session(self, window_id: str) -> None:
        state = self.get_window_state(window_id)
        state.session_id = ""
        state.transcript_path = ""
        self._schedule_save()
        logger.debug("Cleared session for window_id %s", window_id)

    def set_provider_manual_override(self, window_id: str, *, value: bool) -> None:
        state = self.window_states.get(window_id)
        if state is None or state.provider_manual_override == value:
            return
        state.provider_manual_override = value
        self._schedule_save()

    def clear_transcript_path(self, window_id: str) -> None:
        state = self.window_states.get(window_id)
        if state is None or not state.transcript_path:
            return
        state.transcript_path = ""
        self._schedule_save()

    def get_session_id_for_window(self, window_id: str) -> str | None:
        state = self.window_states.get(window_id)
        return state.session_id if state and state.session_id else None

    def has_window(self, window_id: str) -> bool:
        return window_id in self.window_states

    def iter_window_ids(self) -> list[str]:
        return list(self.window_states)

    def remove_window(self, window_id: str) -> bool:
        if window_id not in self.window_states:
            return False
        del self.window_states[window_id]
        self._schedule_save()
        return True

    def get_pane(self, window_id: str, pane_id: str) -> PaneInfo | None:
        state = self.window_states.get(window_id)
        if state is None:
            return None
        return state.panes.get(pane_id)

    def upsert_pane(
        self,
        window_id: str,
        pane_id: str,
        *,
        name: str | None | _Sentinel = _SENTINEL,
        provider: str | None = None,
        last_active_ts: float | None = None,
        state: PaneState | None = None,
        subscribed: bool | None = None,
    ) -> PaneInfo:
        window_state = self.get_window_state(window_id)
        pane = window_state.panes.get(pane_id)
        if pane is None:
            pane = PaneInfo(pane_id=pane_id)
            window_state.panes[pane_id] = pane
        if not isinstance(name, _Sentinel):
            pane.name = name
        if provider is not None:
            pane.provider = provider
        if last_active_ts is not None:
            pane.last_active_ts = last_active_ts
        if state is not None:
            if state not in PANE_STATES:
                raise ValueError(f"Invalid pane state: {state!r}")
            pane.state = state
        if subscribed is not None:
            pane.subscribed = subscribed
        self._schedule_save()
        return pane

    def remove_pane(self, window_id: str, pane_id: str) -> bool:
        state = self.window_states.get(window_id)
        if state is None or pane_id not in state.panes:
            return False
        del state.panes[pane_id]
        self._schedule_save()
        return True

    def get_pane_lifecycle_notify(self, window_id: str, default: bool) -> bool:
        state = self.window_states.get(window_id)
        if state is None or state.pane_lifecycle_notify is None:
            return default
        return state.pane_lifecycle_notify

    def set_pane_lifecycle_notify(self, window_id: str, value: bool | None) -> None:
        state = self.get_window_state(window_id)
        if state.pane_lifecycle_notify == value:
            return
        state.pane_lifecycle_notify = value
        self._schedule_save()

    def set_window_provider(
        self,
        window_id: str,
        provider_name: str,
        *,
        cwd: str | None = None,
        new_provider_supports_hook: bool = True,
    ) -> None:
        state = self.get_window_state(window_id)
        old_provider = state.provider_name
        state.provider_name = provider_name
        if cwd:
            state.cwd = cwd

        if old_provider != provider_name and provider_name:
            if not new_provider_supports_hook and state.session_id:
                state.session_id = ""
                state.transcript_path = ""
            self._on_hookless_provider_switch(window_id)

        self._schedule_save()

    def get_approval_mode(self, window_id: str) -> str:
        state = self.window_states.get(window_id)
        mode = state.approval_mode if state else DEFAULT_APPROVAL_MODE
        return mode if mode in APPROVAL_MODES else DEFAULT_APPROVAL_MODE

    def set_window_approval_mode(self, window_id: str, mode: str) -> None:
        normalized = mode.lower()
        if normalized not in APPROVAL_MODES:
            raise ValueError(f"Invalid approval mode: {mode!r}")
        state = self.get_window_state(window_id)
        state.approval_mode = normalized
        self._schedule_save()

    def get_batch_mode(self, window_id: str) -> str:
        state = self.window_states.get(window_id)
        mode = state.batch_mode if state else DEFAULT_BATCH_MODE
        return mode if mode in BATCH_MODES else DEFAULT_BATCH_MODE

    def set_batch_mode(self, window_id: str, mode: str) -> None:
        if mode not in BATCH_MODES:
            raise ValueError(f"Invalid batch mode: {mode!r}")
        state = self.get_window_state(window_id)
        if state.batch_mode != mode:
            state.batch_mode = mode
            self._schedule_save()

    def cycle_batch_mode(self, window_id: str) -> str:
        current = self.get_batch_mode(window_id)
        new_mode = _BATCH_CYCLE.get(current, "ephemeral")
        self.set_batch_mode(window_id, new_mode)
        return new_mode

    _TOOL_CALL_VISIBILITY_MODES = TOOL_CALL_VISIBILITY_MODES

    def get_tool_call_visibility(self, window_id: str) -> str:
        state = self.window_states.get(window_id)
        return state.tool_call_visibility if state else DEFAULT_TOOL_CALL_VISIBILITY

    def set_tool_call_visibility(self, window_id: str, mode: str) -> None:
        if mode not in self._TOOL_CALL_VISIBILITY_MODES:
            raise ValueError(f"Invalid tool_call_visibility: {mode!r}")
        state = self.get_window_state(window_id)
        if state.tool_call_visibility != mode:
            state.tool_call_visibility = mode
            self._schedule_save()

    def cycle_tool_call_visibility(self, window_id: str) -> str:
        current = self.get_tool_call_visibility(window_id)
        modes = self._TOOL_CALL_VISIBILITY_MODES
        idx = modes.index(current) if current in modes else 0
        new_mode = modes[(idx + 1) % len(modes)]
        self.set_tool_call_visibility(window_id, new_mode)
        return new_mode

    def prune_stale_window_states(
        self,
        live_window_ids: set[str],
        session_map_wids: set[str],
        bound_window_ids: set[str],
    ) -> bool:
        stale = [
            wid
            for wid in self.window_states
            if (
                wid not in session_map_wids
                and wid not in bound_window_ids
                and wid not in live_window_ids
            )
        ]
        if not stale:
            return False
        for wid in stale:
            logger.debug("Pruning stale window_state: %s", wid)
            del self.window_states[wid]
        logger.info("Pruned %d stale window_state(s)", len(stale))
        self._schedule_save()
        return True


_active_store: WindowStateStore | None = None


def is_window_store_wired() -> bool:
    return _active_store is not None


def get_window_store() -> WindowStateStore:
    if _active_store is None:
        raise RuntimeError(
            "WindowStateStore not yet wired. "
            "Instantiate SessionManager() before accessing window_store."
        )
    return _active_store


def install_window_store(store: WindowStateStore) -> None:
    global _active_store
    _active_store = store


class _WindowStoreProxy:
    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(get_window_store(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(get_window_store(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(get_window_store(), name)

    def __repr__(self) -> str:
        if _active_store is None:
            return "<WindowStoreProxy unwired>"
        return f"<WindowStoreProxy → {_active_store!r}>"


window_store: WindowStateStore = cast("WindowStateStore", _WindowStoreProxy())
