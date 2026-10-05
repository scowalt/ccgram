import json
import time
from pathlib import Path

import pytest

import ccgram.session  # noqa: F401


@pytest.fixture(autouse=True)
def _default_replace_prompt_mode():
    from ccgram.config import config

    original = config.prompt_mode
    config.prompt_mode = "replace"
    yield
    config.prompt_mode = original


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    (tmp_path / "session_map.json").write_text("{}")
    (tmp_path / "events.jsonl").write_text("")
    (tmp_path / "state.json").write_text("{}")
    (tmp_path / "monitor_state.json").write_text("{}")

    monkeypatch.setattr(
        "ccgram.config.config.session_map_file", tmp_path / "session_map.json"
    )
    monkeypatch.setattr("ccgram.config.config.events_file", tmp_path / "events.jsonl")
    monkeypatch.setattr(
        "ccgram.config.config.tmux_session_name",
        "ccgram",
    )

    return tmp_path


@pytest.fixture
def write_session_map(state_dir):

    def _write(entries: dict) -> Path:
        path = state_dir / "session_map.json"
        path.write_text(json.dumps(entries))
        return path

    return _write


@pytest.fixture
def append_event(state_dir):

    def _append(
        event_type: str,
        window_key: str = "ccgram:@0",
        session_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        data: dict | None = None,
        timestamp: float | None = None,
    ) -> None:
        line = json.dumps(
            {
                "ts": timestamp or time.time(),
                "event": event_type,
                "window_key": window_key,
                "session_id": session_id,
                "data": data or {},
            },
            separators=(",", ":"),
        )
        events_file = state_dir / "events.jsonl"
        with open(events_file, "a") as f:
            f.write(line + "\n")

    return _append
