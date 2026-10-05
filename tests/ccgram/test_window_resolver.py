from __future__ import annotations

from types import SimpleNamespace

import pytest

from ccgram.window_resolver import (
    LiveWindow,
    is_window_id,
    resolve_stale_ids,
)


class TestIsWindowId:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            pytest.param("@0", True, id="at_zero"),
            pytest.param("@12", True, id="at_multi_digit"),
            pytest.param("@", False, id="at_only"),
            pytest.param("0", False, id="no_at"),
            pytest.param("", False, id="empty"),
            pytest.param("mywindow", False, id="name"),
        ],
    )
    def test_is_window_id(self, key: str, expected: bool) -> None:
        assert is_window_id(key) == expected


def _ws(name: str) -> SimpleNamespace:
    return SimpleNamespace(window_name=name)


def _ws_sid(name: str, session_id: str) -> SimpleNamespace:
    return SimpleNamespace(window_name=name, session_id=session_id)


class TestResolveStaleIds:
    def test_no_changes_when_ids_still_live(self) -> None:
        live = [LiveWindow("@0", "proj")]
        window_states = {"@0": _ws("proj")}
        thread_bindings: dict = {100: {42: "@0"}}
        offsets: dict = {100: {"@0": 10}}
        display_names = {"@0": "proj"}

        changed = resolve_stale_ids(
            live, window_states, thread_bindings, offsets, display_names
        )

        assert not changed
        assert "@0" in window_states
        assert thread_bindings[100][42] == "@0"

    def test_stale_id_remapped_via_display_name(self) -> None:
        live = [LiveWindow("@1", "proj")]
        window_states = {"@0": _ws("proj")}
        thread_bindings: dict = {100: {42: "@0"}}
        offsets: dict = {}
        display_names = {"@0": "proj"}

        changed = resolve_stale_ids(
            live, window_states, thread_bindings, offsets, display_names
        )

        assert changed
        assert "@1" in window_states
        assert "@0" not in window_states
        assert display_names.get("@1") == "proj"
        assert "@0" not in display_names
        assert thread_bindings[100][42] == "@0"

    def test_dead_window_preserved_without_live_match(self) -> None:
        live: list[LiveWindow] = []
        window_states = {"@0": _ws("dead-proj")}
        thread_bindings: dict = {100: {42: "@0"}}
        offsets: dict = {}
        display_names: dict = {}

        changed = resolve_stale_ids(
            live, window_states, thread_bindings, offsets, display_names
        )

        assert not changed
        assert "@0" in window_states
        assert thread_bindings[100][42] == "@0"

    def test_old_format_name_key_migrated_to_window_id(self) -> None:
        live = [LiveWindow("@3", "myproject")]
        window_states = {"myproject": _ws("myproject")}
        thread_bindings: dict = {100: {7: "myproject"}}
        offsets: dict = {}
        display_names: dict = {}

        changed = resolve_stale_ids(
            live, window_states, thread_bindings, offsets, display_names
        )

        assert changed
        assert "@3" in window_states
        assert "myproject" not in window_states
        assert thread_bindings[100][7] == "@3"
        assert display_names.get("@3") == "myproject"

    def test_old_format_name_key_dropped_when_no_live_match(self) -> None:
        live: list[LiveWindow] = []
        window_states = {"oldname": _ws("oldname")}
        thread_bindings: dict = {}
        offsets: dict = {}
        display_names: dict = {}

        changed = resolve_stale_ids(
            live, window_states, thread_bindings, offsets, display_names
        )

        assert changed
        assert "oldname" not in window_states

    def test_empty_user_bindings_pruned(self) -> None:
        live: list[LiveWindow] = []
        window_states: dict = {}
        thread_bindings: dict = {100: {42: "oldname"}}
        offsets: dict = {}
        display_names: dict = {}

        changed = resolve_stale_ids(
            live, window_states, thread_bindings, offsets, display_names
        )

        assert changed
        assert 100 not in thread_bindings

    def test_offsets_dropped_when_display_name_already_remapped(self) -> None:
        live = [LiveWindow("@2", "proj")]
        window_states = {"@0": _ws("proj")}
        thread_bindings: dict = {}
        offsets: dict = {100: {"@0": 99}}
        display_names = {"@0": "proj"}

        changed = resolve_stale_ids(
            live, window_states, thread_bindings, offsets, display_names
        )

        assert changed
        assert "@2" in window_states
        assert offsets[100] == {}

    def test_returns_false_with_empty_state(self) -> None:
        changed = resolve_stale_ids([], {}, {}, {}, {})
        assert not changed


class TestResolveStaleIdsBySession:
    def test_herdr_no_session_match_keeps_dead_window(self) -> None:
        live = [LiveWindow("w3:t1", "other")]
        window_states = {"w2:t1": _ws_sid("ccgram", "S1")}
        thread_bindings: dict = {100: {42: "w2:t1"}}
        offsets: dict = {}
        display_names = {"w2:t1": "ccgram ▸ claude"}

        changed = resolve_stale_ids(
            live,
            window_states,
            thread_bindings,
            offsets,
            display_names,
            ids_stable=False,
            live_session_ids={"w3:t1": "S-other"},
        )

        assert not changed
        assert "w2:t1" in window_states
        assert thread_bindings[100][42] == "w2:t1"

    def test_herdr_live_id_unchanged(self) -> None:
        live = [LiveWindow("w2:t1", "ccgram")]
        window_states = {"w2:t1": _ws_sid("ccgram", "S1")}
        thread_bindings: dict = {100: {42: "w2:t1"}}
        offsets: dict = {}
        display_names = {"w2:t1": "ccgram ▸ claude"}

        changed = resolve_stale_ids(
            live,
            window_states,
            thread_bindings,
            offsets,
            display_names,
            ids_stable=False,
            live_session_ids={"w2:t1": "S1"},
        )

        assert not changed
        assert "w2:t1" in window_states

    def test_herdr_no_live_session_ids_keeps_state(self) -> None:
        live = [LiveWindow("w3:t1", "ccgram")]
        window_states = {"w2:t1": _ws_sid("ccgram", "S1")}
        thread_bindings: dict = {100: {42: "w2:t1"}}
        offsets: dict = {}
        display_names = {"w2:t1": "ccgram"}

        changed = resolve_stale_ids(
            live,
            window_states,
            thread_bindings,
            offsets,
            display_names,
            ids_stable=False,
            live_session_ids=None,
        )

        assert not changed
        assert "w2:t1" in window_states
        assert thread_bindings[100][42] == "w2:t1"

    def test_herdr_skips_dead_session_map_entry(self) -> None:
        live = [LiveWindow("w3:t1", "ccgram")]
        window_states = {"w2:t1": _ws_sid("ccgram", "S1")}
        thread_bindings: dict = {}
        offsets: dict = {}
        display_names = {"w2:t1": "ccgram"}

        changed = resolve_stale_ids(
            live,
            window_states,
            thread_bindings,
            offsets,
            display_names,
            ids_stable=False,
            live_session_ids={"w2:t1": "S1"},
        )

        assert not changed
        assert "w2:t1" in window_states

    def test_herdr_tab_id_restart_remaps_state_binding_and_offset(self) -> None:
        live = [LiveWindow("w3:t1", "ccgram")]
        window_states = {"w2:t1": _ws_sid("ccgram", "T1")}
        thread_bindings: dict = {100: {42: "w2:t1"}}
        offsets: dict = {100: {"w2:t1": 5}}
        display_names = {"w2:t1": "myws ▸ claude"}

        changed = resolve_stale_ids(
            live,
            window_states,
            thread_bindings,
            offsets,
            display_names,
            ids_stable=False,
            live_session_ids={"w3:t1": "T1"},
        )

        assert changed
        assert "w3:t1" in window_states
        assert "w2:t1" not in window_states
        assert thread_bindings[100][42] == "w3:t1"
        assert offsets[100] == {"w3:t1": 5}
        assert display_names.get("w3:t1") == "myws ▸ claude"
        assert "w2:t1" not in display_names

    def test_stable_path_ignores_session_ids(self) -> None:
        live = [LiveWindow("@1", "proj")]
        window_states = {"@0": _ws("proj")}
        thread_bindings: dict = {}
        offsets: dict = {}
        display_names = {"@0": "proj"}

        changed = resolve_stale_ids(
            live,
            window_states,
            thread_bindings,
            offsets,
            display_names,
            ids_stable=True,
            live_session_ids={"@1": "whatever"},
        )

        assert changed
        assert "@1" in window_states
        assert "@0" not in window_states
