from __future__ import annotations

from ccgram.session_map import live_window_session_ids


def test_tmux_key_matches_by_suffix() -> None:
    raw = {"ccgram:@12": {"session_id": "S1", "cwd": "/repo"}}
    assert live_window_session_ids(raw, {"@12"}) == {"@12": "S1"}


def test_herdr_key_with_colon_in_window_id() -> None:
    raw = {"herdr:w2:p1": {"session_id": "S1"}}
    assert live_window_session_ids(raw, {"w2:p1"}) == {"w2:p1": "S1"}


def test_stale_entry_not_live_is_ignored() -> None:
    raw = {
        "herdr:w2:p1": {"session_id": "S1"},
        "herdr:w3:p1": {"session_id": "S2"},
    }
    assert live_window_session_ids(raw, {"w3:p1"}) == {"w3:p1": "S2"}


def test_missing_session_id_skipped() -> None:
    raw = {"herdr:w2:p1": {"cwd": "/repo"}}
    assert live_window_session_ids(raw, {"w2:p1"}) == {}


def test_non_dict_entry_skipped() -> None:
    raw = {"herdr:w2:p1": "garbage", "herdr:w3:p1": {"session_id": "S2"}}
    assert live_window_session_ids(raw, {"w2:p1", "w3:p1"}) == {"w3:p1": "S2"}


def test_similar_ids_do_not_cross_match() -> None:
    raw = {"herdr:w12:p1": {"session_id": "S12"}}
    assert live_window_session_ids(raw, {"w2:p1"}) == {}


def test_empty_inputs() -> None:
    assert live_window_session_ids({}, set()) == {}
    assert live_window_session_ids({}, {"w2:p1"}) == {}


def test_herdr_tab_id_key_matches_by_suffix() -> None:
    raw = {"herdr:w2:t1": {"session_id": "S1"}}
    assert live_window_session_ids(raw, {"w2:t1"}) == {"w2:t1": "S1"}


def test_herdr_tab_id_no_cross_match_on_similar_prefix() -> None:
    raw = {"herdr:w12:t1": {"session_id": "S12"}}
    assert live_window_session_ids(raw, {"w2:t1"}) == {}


def test_herdr_tab_id_stale_pre_restart_entry_ignored() -> None:
    raw = {
        "herdr:w2:t1": {"session_id": "S1"},
        "herdr:w3:t1": {"session_id": "S1"},
    }
    assert live_window_session_ids(raw, {"w3:t1"}) == {"w3:t1": "S1"}
