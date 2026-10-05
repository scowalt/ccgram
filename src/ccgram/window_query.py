from __future__ import annotations

from .window_state_ports import identity_state as _identity_state
from .window_state_ports import lifecycle_state as _lifecycle_state
from .window_state_ports import tool_state as _tool_state
from .window_state_store import window_store
from .window_view import WindowView


def view_window(window_id: str) -> WindowView | None:
    identity = _identity_state.get_identity(window_id)
    if identity is None:
        return None
    lifecycle = _lifecycle_state.get_lifecycle(window_id)
    tools = _tool_state.get_tool_modes(window_id)
    if lifecycle is None or tools is None:
        return None
    return WindowView(
        window_id=window_id,
        cwd=identity.cwd,
        provider_name=identity.provider_name,
        approval_mode=identity.approval_mode,
        batch_mode=tools.batch_mode,
        tool_call_visibility=tools.tool_call_visibility,
        transcript_path=identity.transcript_path,
        window_name=identity.window_name,
        session_id=identity.session_id,
        origin=lifecycle.origin,
    )


def get_window_provider(window_id: str) -> str | None:
    return _identity_state.get_provider_name(window_id)


def get_approval_mode(window_id: str) -> str:
    return _identity_state.get_approval_mode(window_id)


def get_batch_mode(window_id: str) -> str:
    return _tool_state.get_batch_mode(window_id)


def is_ephemeral_tools(window_id: str) -> bool:
    return _tool_state.is_ephemeral_tools(window_id)


def get_tool_call_visibility(window_id: str) -> str:
    return _tool_state.get_tool_call_visibility(window_id)


def is_tool_calls_hidden(window_id: str) -> bool:
    return _tool_state.is_tool_calls_hidden(window_id)


def get_session_id_for_window(window_id: str) -> str | None:
    return window_store.get_session_id_for_window(window_id)


def window_count() -> int:
    return len(window_store.window_states)


def iter_window_ids() -> list[str]:
    return list(window_store.window_states.keys())
