"""Tests for the backend-neutral topic-mapping projection (Task 10).

Covers:
* ``is_agent_topic_window`` — the capability-gated discovery filter that decides
  whether a multiplexer window surfaces as its own Telegram topic.
* herdr topic filtering, where ``window_id`` is the tab id and native agent
  status decides whether the tab should surface as a Telegram topic.
"""

from __future__ import annotations

import pytest

from ccgram.multiplexer.base import MultiplexerCapabilities, WindowRef
from ccgram.multiplexer.topic_mapping import (
    format_agent_topic_prefix,
    is_agent_topic_window,
)
from ccgram.session import SessionManager
from ccgram.session_resolver import session_resolver
from ccgram.thread_router import thread_router
from ccgram.window_state_store import WindowState, window_store

# tmux-like: no native agent status → every window is a topic.
TMUX_CAPS = MultiplexerCapabilities(
    name="tmux",
    ids_stable_across_restart=True,
    exposes_pane_tty=True,
    native_agent_status=False,
    read_max_lines=None,
    self_identify_env="TMUX_PANE",
    supports_event_stream=False,
)

HERDR_CAPS = MultiplexerCapabilities(
    name="herdr",
    ids_stable_across_restart=False,
    exposes_pane_tty=False,
    native_agent_status=True,
    read_max_lines=1000,
    self_identify_env="HERDR_PANE_ID",
    supports_event_stream=True,
)


def _win(window_id: str, command: str = "") -> WindowRef:
    return WindowRef(
        window_id=window_id,
        window_name="",
        cwd="/proj",
        pane_current_command=command,
    )


class TestIsAgentTopicWindow:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("claude", True),  # tmux surfaces agent windows...
            ("", True),  # ...and bare/shell windows alike (unchanged behavior)
            ("zsh", True),
        ],
    )
    def test_tmux_surfaces_every_window(self, command: str, expected: bool) -> None:
        assert is_agent_topic_window(_win("@1", command), TMUX_CAPS) is expected

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("claude", True),  # an agent pane is a topic
            ("codex", True),
            ("", False),  # a bare shell pane is NOT a topic on herdr
            ("   ", False),  # whitespace-only label is not an agent
        ],
    )
    def test_herdr_only_agent_tabs(self, command: str, expected: bool) -> None:
        assert is_agent_topic_window(_win("w2:t1", command), HERDR_CAPS) is expected

    def test_herdr_distinct_tabs_are_distinct_topics(self) -> None:
        tab_a = _win("w2:t1", "claude")
        tab_b = _win("w2:t2", "claude")
        assert is_agent_topic_window(tab_a, HERDR_CAPS) is True
        assert is_agent_topic_window(tab_b, HERDR_CAPS) is True
        assert tab_a.window_id != tab_b.window_id


class TestFormatAgentTopicPrefix:
    @pytest.mark.parametrize(
        ("workspace", "tab", "expected"),
        [
            # Lone tab → "<workspace> ▸ <tab>".
            ("ccgram", "herdr-support", "ccgram ▸ herdr-support"),
            ("ccgram", "ralphex", "ccgram ▸ ralphex"),
            # Same workspace, different tab labels → distinct titles (no collision).
            ("ccgram", "herdr-support", "ccgram ▸ herdr-support"),
            ("ccgram", "ralphex", "ccgram ▸ ralphex"),
            # Numeric / auto-generated tab labels still render usefully.
            ("myproject", "tab-1", "myproject ▸ tab-1"),
            ("myproject", "Tab 1", "myproject ▸ Tab 1"),
            # Shell tab (no agent) renders the same way — label is tab name.
            ("ccgram", "zsh", "ccgram ▸ zsh"),
            # Missing parts degrade without a stray separator.
            ("", "herdr-support", "herdr-support"),
            ("ccgram", "", "ccgram"),
            ("", "", ""),
            # Whitespace is trimmed off every part.
            ("  ccgram  ", "  herdr-support  ", "ccgram ▸ herdr-support"),
        ],
    )
    def test_renders_workspace_tab_label(
        self, workspace: str, tab: str, expected: str
    ) -> None:
        assert format_agent_topic_prefix(workspace, tab) == expected

    def test_same_agent_different_tabs_are_distinct(self) -> None:
        """Two tabs in the same workspace with different labels produce distinct titles.

        This is the core requirement: "ccgram ▸ herdr-support" and
        "ccgram ▸ ralphex" are distinct even when both run claude.
        """
        label_a = format_agent_topic_prefix("ccgram", "herdr-support")
        label_b = format_agent_topic_prefix("ccgram", "ralphex")
        assert label_a == "ccgram ▸ herdr-support"
        assert label_b == "ccgram ▸ ralphex"
        assert label_a != label_b

    def test_rename_changes_label_not_identity(self) -> None:
        """Renaming the workspace re-renders the label; the tab id is the key."""
        before = format_agent_topic_prefix("ccgram", "herdr-support")
        after = format_agent_topic_prefix("ccgram-v2", "herdr-support")
        assert before == "ccgram ▸ herdr-support"
        assert after == "ccgram-v2 ▸ herdr-support"
        assert before != after


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    thread_router.reset()
    window_store.window_states.clear()
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


class TestHerdrTabRouting:
    def test_two_tabs_route_to_distinct_topics(self, mgr: SessionManager) -> None:
        thread_router.bind_thread(100, 11, "w2:t1")
        thread_router.bind_thread(100, 12, "w2:t2")
        window_store.window_states["w2:t1"] = WindowState(
            session_id="sess-A", cwd="/proj"
        )
        window_store.window_states["w2:t2"] = WindowState(
            session_id="sess-B", cwd="/proj"
        )

        assert session_resolver.find_users_for_session("sess-A") == [(100, "w2:t1", 11)]
        assert session_resolver.find_users_for_session("sess-B") == [(100, "w2:t2", 12)]

    def test_binding_is_keyed_per_tab(self, mgr: SessionManager) -> None:
        thread_router.bind_thread(100, 11, "w2:t1")
        thread_router.bind_thread(100, 12, "w2:t2")

        assert thread_router.get_window_for_thread(100, 11) == "w2:t1"
        assert thread_router.get_window_for_thread(100, 12) == "w2:t2"
        assert thread_router.get_thread_for_window(100, "w2:t1") == 11
        assert thread_router.get_thread_for_window(100, "w2:t2") == 12

    def test_no_stream_crosstalk_between_tabs(self, mgr: SessionManager) -> None:
        thread_router.bind_thread(100, 11, "w2:t1")
        thread_router.bind_thread(100, 12, "w2:t2")
        window_store.window_states["w2:t1"] = WindowState(
            session_id="sess-A", cwd="/proj"
        )
        window_store.window_states["w2:t2"] = WindowState(
            session_id="sess-B", cwd="/proj"
        )

        threads_for_a = {
            thread_id
            for _, _, thread_id in session_resolver.find_users_for_session("sess-A")
        }
        assert threads_for_a == {11}
