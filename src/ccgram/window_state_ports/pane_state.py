from __future__ import annotations

from dataclasses import dataclass

from ..window_state_store import (
    DEFAULT_PANE_STATE,
    PANE_STATES,
    PaneInfo,
    PaneState,
    window_store,
)


@dataclass(frozen=True, slots=True)
class PaneProjection:
    pane_id: str
    name: str | None
    provider: str
    last_active_ts: float
    state: PaneState
    subscribed: bool

    @classmethod
    def from_pane(cls, pane: PaneInfo) -> PaneProjection:
        return cls(
            pane_id=pane.pane_id,
            name=pane.name,
            provider=pane.provider,
            last_active_ts=pane.last_active_ts,
            state=pane.state,
            subscribed=pane.subscribed,
        )


@dataclass(frozen=True, slots=True)
class WindowPaneSnapshot:
    window_id: str
    panes: tuple[PaneProjection, ...]
    pane_lifecycle_notify: bool | None


def get_pane_projection(window_id: str, pane_id: str) -> PaneProjection | None:
    pane = window_store.get_pane(window_id, pane_id)
    if pane is None:
        return None
    return PaneProjection.from_pane(pane)


def list_pane_projections(window_id: str) -> tuple[PaneProjection, ...]:
    state = window_store.window_states.get(window_id)
    if state is None or not state.panes:
        return ()
    return tuple(PaneProjection.from_pane(p) for p in state.panes.values())


def snapshot_window_panes(window_id: str) -> WindowPaneSnapshot | None:
    state = window_store.window_states.get(window_id)
    if state is None:
        return None
    panes = tuple(PaneProjection.from_pane(p) for p in state.panes.values())
    return WindowPaneSnapshot(
        window_id=window_id,
        panes=panes,
        pane_lifecycle_notify=state.pane_lifecycle_notify,
    )


def get_pane_lifecycle_notify(window_id: str, default: bool) -> bool:
    return window_store.get_pane_lifecycle_notify(window_id, default)


def upsert_pane(
    window_id: str,
    pane_id: str,
    *,
    name: str | None | object = ...,
    provider: str | None = None,
    last_active_ts: float | None = None,
    state: PaneState | None = None,
    subscribed: bool | None = None,
) -> PaneProjection:
    if state is not None and state not in PANE_STATES:
        raise ValueError(f"Invalid pane state: {state!r}")
    kwargs: dict[str, object] = {}
    if name is not ...:
        kwargs["name"] = name
    if provider is not None:
        kwargs["provider"] = provider
    if last_active_ts is not None:
        kwargs["last_active_ts"] = last_active_ts
    if state is not None:
        kwargs["state"] = state
    if subscribed is not None:
        kwargs["subscribed"] = subscribed
    pane = window_store.upsert_pane(window_id, pane_id, **kwargs)  # type: ignore[arg-type]
    return PaneProjection.from_pane(pane)


def remove_pane(window_id: str, pane_id: str) -> bool:
    return window_store.remove_pane(window_id, pane_id)


def set_pane_lifecycle_notify(window_id: str, value: bool | None) -> None:
    window_store.set_pane_lifecycle_notify(window_id, value)


__all__ = [
    "DEFAULT_PANE_STATE",
    "PANE_STATES",
    "PaneProjection",
    "WindowPaneSnapshot",
    "get_pane_lifecycle_notify",
    "get_pane_projection",
    "list_pane_projections",
    "remove_pane",
    "set_pane_lifecycle_notify",
    "snapshot_window_panes",
    "upsert_pane",
]
