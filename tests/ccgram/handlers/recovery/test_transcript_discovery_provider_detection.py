from unittest.mock import AsyncMock

from ccgram.handlers.recovery import transcript_discovery as td
from ccgram.multiplexer.base import WindowRef
from ccgram.window_state_ports import identity_state


def _identity(window_id: str) -> identity_state.IdentityProjection:
    return identity_state.IdentityProjection(
        window_id=window_id,
        cwd="/repo",
        session_id="",
        transcript_path=None,
        provider_name="",
        window_name="agent",
        approval_mode="default",
    )


async def test_empty_pane_command_still_runs_foreground_provider_detection(
    monkeypatch,
) -> None:
    detect = AsyncMock(return_value="")
    monkeypatch.setattr(td, "detect_provider_from_pane", detect)
    monkeypatch.setattr(
        td.identity_state, "get_identity", lambda window_id: _identity(window_id)
    )
    monkeypatch.setattr(td, "_resolve_providers_to_try", lambda *_args: [])

    await td.discover_and_register_transcript(
        "w2:t1",
        _window=WindowRef(
            window_id="w2:t1",
            window_name="agent",
            cwd="/repo",
            pane_current_command="",
        ),
    )

    detect.assert_awaited_once_with("", window_id="w2:t1")
