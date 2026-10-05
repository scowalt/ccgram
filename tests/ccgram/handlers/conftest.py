from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _instant_session_map_wait(monkeypatch):
    from ccgram.session_map import SessionMapSync, install_session_map_sync

    dummy = SessionMapSync(schedule_save=lambda: None)
    dummy.wait_for_session_map_entry = AsyncMock(return_value=True)
    install_session_map_sync(dummy)


@pytest.fixture(autouse=True)
def _wire_runtime_callbacks_for_tests():
    from ccgram import bootstrap
    from ccgram.handlers.shell import shell_capture

    bootstrap.reset_for_testing()

    shell_capture.register_approval_callback(AsyncMock(return_value=False))

    yield

    bootstrap.reset_for_testing()


@pytest.fixture(autouse=True)
def _disable_send_rate_limit(monkeypatch):
    monkeypatch.setattr(
        "ccgram.handlers.messaging_pipeline.message_sender.MESSAGE_SEND_INTERVAL", 0
    )


@pytest.fixture(autouse=True)
def _zero_command_orchestration_delays(monkeypatch):
    monkeypatch.setattr(
        "ccgram.handlers.commands.status_snapshot._CODEX_STATUS_FALLBACK_DELAY_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "ccgram.handlers.commands.failure_probe._COMMAND_ERROR_PROBE_DELAY_SECONDS",
        0,
    )
