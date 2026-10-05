from __future__ import annotations

from dataclasses import dataclass

from ..window_state_store import (
    DEFAULT_WINDOW_ORIGIN,
    WINDOW_ORIGINS,
    window_store,
)


@dataclass(frozen=True, slots=True)
class LifecycleProjection:
    window_id: str
    origin: str


def get_lifecycle(window_id: str) -> LifecycleProjection | None:
    state = window_store.window_states.get(window_id)
    if state is None:
        return None
    origin = state.origin if state.origin in WINDOW_ORIGINS else DEFAULT_WINDOW_ORIGIN
    return LifecycleProjection(window_id=window_id, origin=origin)


def get_origin(window_id: str) -> str:
    state = window_store.window_states.get(window_id)
    if state is None:
        return DEFAULT_WINDOW_ORIGIN
    return state.origin if state.origin in WINDOW_ORIGINS else DEFAULT_WINDOW_ORIGIN


def set_window_origin(window_id: str, origin: str) -> None:
    window_store.set_window_origin(window_id, origin)


__all__ = [
    "LifecycleProjection",
    "get_lifecycle",
    "get_origin",
    "set_window_origin",
]
