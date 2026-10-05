from __future__ import annotations

import inspect

import pytest

from ccgram.multiplexer import UnknownMultiplexerError, get_multiplexer
from ccgram.multiplexer.base import Multiplexer, MultiplexerCapabilities

CANDIDATE_BACKENDS = ["tmux", "herdr"]

CONTRACT_METHODS = (
    "ensure_session",
    "list_windows",
    "capture_scrollback",
    "pane_dims",
    "send",
    "send_to_pane",
    "kill_window",
    "rename_window",
    "list_panes",
    "create_window",
    "create_worktree_window",
    "foreground",
    "agent_status",
    "split_window",
    "find_window_by_id",
    "capture_pane",
    "capture_pane_by_id",
    "capture_pane_scrollback",
    "send_keys",
    "send_keys_to_pane",
    "get_pane_title",
    "stamp_pane_title",
)


def _backend_or_skip(name: str) -> Multiplexer:
    try:
        return get_multiplexer(name)
    except UnknownMultiplexerError:
        pytest.skip(f"multiplexer backend {name!r} not registered")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"multiplexer backend {name!r} unavailable: {exc}")


@pytest.fixture(params=CANDIDATE_BACKENDS)
def backend(request: pytest.FixtureRequest) -> Multiplexer:
    return _backend_or_skip(request.param)


def test_backend_satisfies_protocol(backend: Multiplexer) -> None:
    assert isinstance(backend, Multiplexer)


def test_backend_exposes_every_contract_method(backend: Multiplexer) -> None:
    for method in CONTRACT_METHODS:
        attr = getattr(backend, method, None)
        assert attr is not None, f"missing contract method {method!r}"
        assert callable(attr), f"contract method {method!r} is not callable"
        assert inspect.iscoroutinefunction(attr), f"{method!r} must be async"


def test_backend_watch_events_is_async_generator(backend: Multiplexer) -> None:
    assert inspect.isasyncgenfunction(backend.watch_events), (
        "watch_events must be an async generator"
    )


async def test_tmux_watch_events_yields_nothing() -> None:
    events = [event async for event in get_multiplexer("tmux").watch_events([])]
    assert events == []


def test_backend_capabilities_shape(backend: Multiplexer) -> None:
    caps = backend.capabilities
    assert isinstance(caps, MultiplexerCapabilities)
    assert isinstance(caps.name, str) and caps.name
    assert isinstance(caps.ids_stable_across_restart, bool)
    assert isinstance(caps.exposes_pane_tty, bool)
    assert isinstance(caps.native_agent_status, bool)
    assert caps.read_max_lines is None or isinstance(caps.read_max_lines, int)
    assert isinstance(caps.self_identify_env, str) and caps.self_identify_env
    assert isinstance(caps.supports_event_stream, bool)
    assert isinstance(caps.native_worktrees, bool)


def test_tmux_capability_values() -> None:
    caps = get_multiplexer("tmux").capabilities
    assert caps.name == "tmux"
    assert caps.ids_stable_across_restart is True
    assert caps.exposes_pane_tty is True
    assert caps.native_agent_status is False
    assert caps.read_max_lines is None
    assert caps.self_identify_env == "TMUX_PANE"
    assert caps.supports_event_stream is False
    assert caps.native_worktrees is False


async def test_tmux_agent_status_returns_none() -> None:
    status = await get_multiplexer("tmux").agent_status("@0")
    assert status is None


async def test_tmux_create_worktree_window_unsupported() -> None:
    ok, msg, name, win_id = await get_multiplexer("tmux").create_worktree_window(
        "/repo", "/repo.worktrees/x", "ccg/x"
    )
    assert ok is False
    assert (name, win_id) == ("", "")
    assert "tmux" in msg


def test_herdr_capability_values() -> None:
    caps = get_multiplexer("herdr").capabilities
    assert caps.name == "herdr"
    assert caps.ids_stable_across_restart is False
    assert caps.exposes_pane_tty is False
    assert caps.native_agent_status is True
    assert caps.read_max_lines == 1000
    assert caps.self_identify_env == "HERDR_PANE_ID"
    assert caps.supports_event_stream is True
    assert caps.native_worktrees is True
