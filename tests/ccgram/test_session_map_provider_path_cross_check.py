from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ccgram import session_map as session_map_module
from ccgram.session_map import SessionMapSync
from ccgram.thread_router import ThreadRouter, install_thread_router
from ccgram.window_state_store import (
    WindowState,
    WindowStateStore,
    install_window_store,
)


@pytest.fixture
def store() -> WindowStateStore:
    s = WindowStateStore(
        schedule_save=lambda: None,
        on_hookless_provider_switch=lambda _wid: None,
    )
    install_window_store(s)
    install_thread_router(
        ThreadRouter(schedule_save=lambda: None, has_window_state=lambda _wid: True)
    )
    return s


def _sync() -> SessionMapSync:
    return SessionMapSync(schedule_save=lambda: None)


def _info(session_id: str, transcript: Path, provider_name: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "cwd": "/repo",
        "window_name": "repo",
        "transcript_path": str(transcript),
        "provider_name": provider_name,
    }


def test_transcript_path_wins_when_session_map_provider_is_stale(
    tmp_path: Path, store: WindowStateStore
) -> None:
    claude_dir = tmp_path / ".claude" / "projects" / "repo"
    claude_dir.mkdir(parents=True)
    transcript = claude_dir / "session.jsonl"
    transcript.write_text('{"type":"assistant"}\n')

    changed = _sync()._sync_window_from_session_map(
        "@9729", _info("session-a", transcript, provider_name="codex")
    )

    assert changed is True
    state = store.window_states["@9729"]
    assert state.provider_name == "claude"
    assert state.transcript_path == str(transcript)


def test_session_map_provider_kept_when_transcript_path_agrees(
    tmp_path: Path, store: WindowStateStore
) -> None:
    codex_dir = tmp_path / ".codex" / "sessions" / "repo"
    codex_dir.mkdir(parents=True)
    transcript = codex_dir / "session.jsonl"
    transcript.write_text("{}\n")

    _sync()._sync_window_from_session_map(
        "@1", _info("session-a", transcript, provider_name="codex")
    )

    assert store.window_states["@1"].provider_name == "codex"


def test_session_map_provider_kept_when_transcript_path_is_ambiguous(
    tmp_path: Path, store: WindowStateStore
) -> None:
    transcript = tmp_path / "weird" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")

    _sync()._sync_window_from_session_map(
        "@1", _info("session-a", transcript, provider_name="codex")
    )

    assert store.window_states["@1"].provider_name == "codex"


def test_repeated_sync_with_stale_claim_is_idempotent_and_silent(
    tmp_path: Path,
    store: WindowStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_dir = tmp_path / ".claude" / "projects" / "repo"
    claude_dir.mkdir(parents=True)
    transcript = claude_dir / "session.jsonl"
    transcript.write_text("{}\n")

    warnings: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        session_map_module.logger,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    stale_info = _info("session-a", transcript, provider_name="codex")
    sync = _sync()

    first = sync._sync_window_from_session_map("@1", stale_info)
    assert first is True
    assert store.window_states["@1"].provider_name == "claude"
    assert len(warnings) == 1

    second = sync._sync_window_from_session_map("@1", stale_info)
    assert second is False
    assert store.window_states["@1"].provider_name == "claude"
    assert len(warnings) == 1


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/Users/x/.claude/projects/repo/abc.jsonl", "claude"),
        ("/Users/x/.claude-team/projects/repo/abc.jsonl", "claude"),
        ("/Users/x/.claude.local/projects/repo/abc.jsonl", "claude"),
        ("/Users/x/.cc-mirror/projects/repo/abc.jsonl", ""),
        ("/Users/x/.codex/sessions/repo/abc.jsonl", "codex"),
        ("/Users/x/.gemini/chats/repo/abc.json", "gemini"),
        ("/Users/x/.pi/agent/sessions/--repo--/abc.jsonl", "pi"),
        ("/Users/x/weird/abc.jsonl", ""),
    ],
)
def test_detect_provider_from_transcript_path(path: str, expected: str) -> None:
    from ccgram.providers import detect_provider_from_transcript_path

    assert detect_provider_from_transcript_path(path) == expected


def test_claude_wrapper_transcript_path_silences_session_map_cross_check(
    tmp_path: Path,
    store: WindowStateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = tmp_path / ".claude-team" / "projects" / "repo" / "abc.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")

    store.window_states["@9730"] = WindowState(
        session_id="abc",
        cwd="/repo",
        window_name="repo",
        transcript_path=str(transcript),
        provider_name="claude",
    )

    warnings: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        session_map_module.logger,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    sync = _sync()
    sync._sync_window_from_session_map(
        "@9730", _info("abc", transcript, provider_name="codex")
    )
    assert store.window_states["@9730"].provider_name == "claude"
    assert warnings == []

    sync._sync_window_from_session_map(
        "@9730", _info("abc", transcript, provider_name="codex")
    )
    assert store.window_states["@9730"].provider_name == "claude"
    assert warnings == []


def test_existing_transcript_path_used_when_info_omits_it(
    tmp_path: Path, store: WindowStateStore
) -> None:
    claude_dir = tmp_path / ".claude" / "projects" / "repo"
    claude_dir.mkdir(parents=True)
    transcript = claude_dir / "session.jsonl"
    transcript.write_text("{}\n")

    store.window_states["@1"] = WindowState(
        session_id="session-a",
        cwd="/repo",
        transcript_path=str(transcript),
        provider_name="claude",
    )

    _sync()._sync_window_from_session_map(
        "@1",
        {
            "session_id": "session-a",
            "cwd": "/repo",
            "window_name": "repo",
            "transcript_path": "",
            "provider_name": "codex",
        },
    )

    assert store.window_states["@1"].provider_name == "claude"
