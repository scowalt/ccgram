from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..window_state_store import (
    APPROVAL_MODES,
    DEFAULT_APPROVAL_MODE,
    window_store,
)


@dataclass(frozen=True, slots=True)
class IdentityProjection:
    window_id: str
    provider_name: str
    session_id: str
    cwd: str
    transcript_path: Path | None
    window_name: str
    approval_mode: str


def get_identity(window_id: str) -> IdentityProjection | None:
    state = window_store.window_states.get(window_id)
    if state is None:
        return None
    return IdentityProjection(
        window_id=window_id,
        provider_name=state.provider_name,
        session_id=state.session_id,
        cwd=state.cwd or "",
        transcript_path=(
            Path(state.transcript_path) if state.transcript_path else None
        ),
        window_name=state.window_name,
        approval_mode=(
            state.approval_mode
            if state.approval_mode in APPROVAL_MODES
            else DEFAULT_APPROVAL_MODE
        ),
    )


def get_provider_name(window_id: str) -> str | None:
    state = window_store.window_states.get(window_id)
    return state.provider_name if state else None


def get_session_id(window_id: str) -> str | None:
    state = window_store.window_states.get(window_id)
    if state is None:
        return None
    sid = state.session_id
    return sid if sid else None


def get_cwd(window_id: str) -> str:
    state = window_store.window_states.get(window_id)
    return state.cwd if state else ""


def get_transcript_path(window_id: str) -> str:
    state = window_store.window_states.get(window_id)
    return state.transcript_path if state else ""


def get_window_name(window_id: str) -> str:
    state = window_store.window_states.get(window_id)
    return state.window_name if state else ""


def get_approval_mode(window_id: str) -> str:
    state = window_store.window_states.get(window_id)
    mode = state.approval_mode if state else DEFAULT_APPROVAL_MODE
    return mode if mode in APPROVAL_MODES else DEFAULT_APPROVAL_MODE


def set_window_approval_mode(window_id: str, mode: str) -> None:
    window_store.set_window_approval_mode(window_id, mode)


def is_provider_manually_overridden(window_id: str) -> bool:
    state = window_store.window_states.get(window_id)
    return state is not None and state.provider_manual_override is True


def set_provider_manual_override(window_id: str, *, value: bool) -> None:
    window_store.set_provider_manual_override(window_id, value=value)


def clear_transcript_path(window_id: str) -> None:
    window_store.clear_transcript_path(window_id)


__all__ = [
    "IdentityProjection",
    "clear_transcript_path",
    "get_approval_mode",
    "get_cwd",
    "get_identity",
    "get_provider_name",
    "get_session_id",
    "get_transcript_path",
    "get_window_name",
    "is_provider_manually_overridden",
    "set_provider_manual_override",
    "set_window_approval_mode",
]
