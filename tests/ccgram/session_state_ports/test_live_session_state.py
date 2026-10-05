from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ccgram.claude_task_state import ClaudeTaskSnapshot
from ccgram.session_state_ports.live_session_state import (
    LiveSessionSnapshot,
    get_last_activity_ts,
    get_live_session_snapshot,
    get_session_id,
    get_task_snapshot,
    get_wait_header,
    has_task_snapshot,
)

_TASK_SNAPSHOT_FN = "ccgram.claude_task_state.get_claude_task_snapshot"
_WAIT_HEADER_FN = "ccgram.claude_task_state.get_claude_wait_header"
_TASK_STATE_STORE = "ccgram.claude_task_state.claude_task_state"
_SESSION_LIFECYCLE = "ccgram.session_lifecycle.session_lifecycle"
_GET_ACTIVE_MONITOR = "ccgram.session_monitor.get_active_monitor"


def test_get_task_snapshot_missing_window() -> None:
    with patch(_TASK_SNAPSHOT_FN, return_value=None):
        assert get_task_snapshot("@99") is None


def test_get_task_snapshot_returns_snapshot() -> None:
    snap = ClaudeTaskSnapshot(items=(), done_count=0, open_count=0)
    with patch(_TASK_SNAPSHOT_FN, return_value=snap):
        assert get_task_snapshot("@1") is snap


def test_task_snapshot_defaults_empty_items() -> None:
    snap = ClaudeTaskSnapshot(items=(), done_count=0, open_count=0)
    assert snap.total_count == 0
    assert snap.done_count == 0
    assert snap.open_count == 0
    assert snap.active_task_id is None


def test_has_task_snapshot_false_when_missing() -> None:
    mock_store = MagicMock()
    mock_store.has_snapshot.return_value = False
    with patch(_TASK_STATE_STORE, mock_store):
        assert has_task_snapshot("@99") is False


def test_has_task_snapshot_true_when_present() -> None:
    mock_store = MagicMock()
    mock_store.has_snapshot.return_value = True
    with patch(_TASK_STATE_STORE, mock_store):
        assert has_task_snapshot("@1") is True


def test_get_wait_header_none_when_missing() -> None:
    with patch(_WAIT_HEADER_FN, return_value=None):
        assert get_wait_header("@99") is None


def test_get_wait_header_returns_value() -> None:
    with patch(_WAIT_HEADER_FN, return_value="Waiting for input"):
        assert get_wait_header("@1") == "Waiting for input"


def test_get_session_id_none_for_hookless_provider() -> None:
    mock_lc = MagicMock()
    mock_lc.resolve_session_id.return_value = None
    with patch(_SESSION_LIFECYCLE, mock_lc):
        assert get_session_id("@hookless") is None


def test_get_session_id_returns_session() -> None:
    mock_lc = MagicMock()
    mock_lc.resolve_session_id.return_value = "uuid-abc"
    with patch(_SESSION_LIFECYCLE, mock_lc):
        assert get_session_id("@1") == "uuid-abc"


def test_get_last_activity_ts_none_when_no_session() -> None:
    mock_lc = MagicMock()
    mock_lc.resolve_session_id.return_value = None
    with (
        patch(_SESSION_LIFECYCLE, mock_lc),
        patch(_GET_ACTIVE_MONITOR, return_value=None),
    ):
        assert get_last_activity_ts("@99") is None


def test_get_last_activity_ts_none_when_monitor_not_started() -> None:
    mock_lc = MagicMock()
    mock_lc.resolve_session_id.return_value = "uuid-xyz"
    with (
        patch(_SESSION_LIFECYCLE, mock_lc),
        patch(_GET_ACTIVE_MONITOR, return_value=None),
    ):
        assert get_last_activity_ts("@1") is None


def test_get_last_activity_ts_stale_transcript_no_activity() -> None:
    mock_lc = MagicMock()
    mock_lc.resolve_session_id.return_value = "uuid-xyz"
    mock_monitor = MagicMock()
    mock_monitor.get_last_activity.return_value = None
    with (
        patch(_SESSION_LIFECYCLE, mock_lc),
        patch(_GET_ACTIVE_MONITOR, return_value=mock_monitor),
    ):
        assert get_last_activity_ts("@1") is None
        mock_monitor.get_last_activity.assert_called_once_with("uuid-xyz")


def test_get_last_activity_ts_returns_timestamp() -> None:
    mock_lc = MagicMock()
    mock_lc.resolve_session_id.return_value = "uuid-xyz"
    mock_monitor = MagicMock()
    mock_monitor.get_last_activity.return_value = 12345.6
    with (
        patch(_SESSION_LIFECYCLE, mock_lc),
        patch(_GET_ACTIVE_MONITOR, return_value=mock_monitor),
    ):
        assert get_last_activity_ts("@1") == pytest.approx(12345.6)


def test_get_live_session_snapshot_all_none() -> None:
    mock_lc = MagicMock()
    mock_lc.resolve_session_id.return_value = None
    with (
        patch(_SESSION_LIFECYCLE, mock_lc),
        patch(_GET_ACTIVE_MONITOR, return_value=None),
        patch(_TASK_SNAPSHOT_FN, return_value=None),
        patch(_WAIT_HEADER_FN, return_value=None),
    ):
        snap = get_live_session_snapshot("@99")

    assert isinstance(snap, LiveSessionSnapshot)
    assert snap.window_id == "@99"
    assert snap.session_id is None
    assert snap.task_snapshot is None
    assert snap.wait_header is None
    assert snap.last_activity_ts is None


def test_get_live_session_snapshot_populated() -> None:
    task_snap = ClaudeTaskSnapshot(items=(), done_count=0, open_count=0)
    mock_lc = MagicMock()
    mock_lc.resolve_session_id.return_value = "uuid-abc"
    mock_monitor = MagicMock()
    mock_monitor.get_last_activity.return_value = 99.9
    with (
        patch(_SESSION_LIFECYCLE, mock_lc),
        patch(_GET_ACTIVE_MONITOR, return_value=mock_monitor),
        patch(_TASK_SNAPSHOT_FN, return_value=task_snap),
        patch(_WAIT_HEADER_FN, return_value="Waiting for input"),
    ):
        snap = get_live_session_snapshot("@1")

    assert snap.window_id == "@1"
    assert snap.session_id == "uuid-abc"
    assert snap.task_snapshot is task_snap
    assert snap.wait_header == "Waiting for input"
    assert snap.last_activity_ts == pytest.approx(99.9)


def test_get_live_session_snapshot_propagates_delegate_exception() -> None:
    with (
        patch(_TASK_SNAPSHOT_FN, side_effect=RuntimeError("delegate failure")),
        pytest.raises(RuntimeError, match="delegate failure"),
    ):
        get_live_session_snapshot("@test-window")


def test_live_session_snapshot_is_frozen() -> None:
    snap = LiveSessionSnapshot(
        window_id="@1",
        session_id=None,
        task_snapshot=None,
        wait_header=None,
        last_activity_ts=None,
    )
    with pytest.raises((AttributeError, TypeError)):
        snap.window_id = "@2"  # type: ignore[misc]
