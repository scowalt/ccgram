from pathlib import Path
from unittest.mock import patch

import pytest

from ._helpers import TEST_CHAT_ID, _bump_message_id

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _reset_runtime_callbacks():
    from ccgram import bootstrap

    bootstrap.reset_for_testing()
    yield
    bootstrap.reset_for_testing()


@pytest.fixture
def e2e_state_dir(tmp_path, monkeypatch):
    state_dir = tmp_path / "ccgram-state"
    state_dir.mkdir()

    state_file = state_dir / "state.json"
    session_map = state_dir / "session_map.json"
    events_file = state_dir / "events.jsonl"
    monitor_state = state_dir / "monitor_state.json"

    state_file.write_text("{}")
    session_map.write_text("{}")
    events_file.write_text("")
    monitor_state.write_text("{}")

    from ccgram.config import config

    monkeypatch.setattr(config, "config_dir", state_dir)
    monkeypatch.setattr(config, "state_file", state_file)
    monkeypatch.setattr(config, "session_map_file", session_map)
    monkeypatch.setattr(config, "events_file", events_file)
    monkeypatch.setattr(config, "monitor_state_file", monitor_state)

    return state_dir


E2E_TMUX_SESSION = "ccgram-e2e"


@pytest.fixture
def e2e_tmux(monkeypatch):
    import libtmux

    from ccgram.config import config
    from ccgram.multiplexer.tmux import TmuxManager

    monkeypatch.setattr(config, "tmux_session_name", E2E_TMUX_SESSION)

    server = libtmux.Server()

    existing = server.sessions.get(session_name=E2E_TMUX_SESSION, default=None)
    if existing:
        existing.kill()

    session = server.new_session(
        session_name=E2E_TMUX_SESSION,
        start_directory=str(Path.home()),
    )
    if session.windows:
        session.windows[0].rename_window("__main__")

    manager = TmuxManager(session_name=E2E_TMUX_SESSION)

    from ccgram.multiplexer import _reset_multiplexer_for_testing, install_multiplexer
    from ccgram.multiplexer import registry as multiplexer_registry
    from ccgram.multiplexer import tmux as tmux_backend

    monkeypatch.setattr(tmux_backend, "tmux_manager", manager)
    multiplexer_registry._reset_multiplexer_cache_for_testing()
    monkeypatch.setitem(multiplexer_registry._instances, "tmux", manager)
    install_multiplexer(manager)

    _tm_modules = [
        "ccgram.bot",
        "ccgram.session",
        "ccgram.session_monitor",
        "ccgram.handlers.text.text_handler",
        "ccgram.handlers.topics.directory_callbacks",
        "ccgram.handlers.polling.polling_coordinator",
        "ccgram.handlers.recovery.recovery_callbacks",
        "ccgram.handlers.sessions_dashboard",
        "ccgram.handlers.live.screenshot_callbacks",
        "ccgram.handlers.interactive.interactive_ui",
        "ccgram.handlers.interactive.interactive_callbacks",
        "ccgram.handlers.topics.window_callbacks",
        "ccgram.handlers.recovery.restore_command",
        "ccgram.handlers.recovery.resume_command",
        "ccgram.handlers.recovery.history_callbacks",
        "ccgram.handlers.sync_command",
        "ccgram.handlers.commands.forward",
        "ccgram.handlers.topics.topic_orchestration",
        "ccgram.handlers.shell.shell_commands",
        "ccgram.handlers.cleanup",
    ]
    import importlib

    for mod_name in _tm_modules:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "tmux_manager"):
            monkeypatch.setattr(mod, "tmux_manager", manager)

    yield manager

    test_session = server.sessions.get(session_name=E2E_TMUX_SESSION, default=None)
    if test_session:
        test_session.kill()
    multiplexer_registry._reset_multiplexer_cache_for_testing()
    _reset_multiplexer_for_testing()


@pytest.fixture
def intercepted_calls():
    return []


_GET_ME_RESPONSE = {
    "id": 1,
    "first_name": "E2EBot",
    "is_bot": True,
    "username": "e2e_testbot",
    "can_join_groups": True,
    "can_read_all_group_messages": True,
    "supports_inline_queries": False,
}

_GET_CHAT_RESPONSE = {
    "id": TEST_CHAT_ID,
    "type": "supergroup",
    "title": "Test",
    "is_forum": True,
}

_PASSTHROUGH_ENDPOINTS = frozenset(
    {
        "setMyCommands",
        "deleteMyCommands",
        "editForumTopic",
        "sendChatAction",
        "unpinAllForumTopicMessages",
        "deleteMessage",
        "answerCallbackQuery",
    }
)


def _parse_request_data(request_data):
    if request_data is None:
        return {}
    if hasattr(request_data, "parameters"):
        return dict(request_data.parameters)
    if isinstance(request_data, dict):
        return request_data
    return {}


def _make_message_response(data, msg_id=None):
    return {
        "message_id": msg_id or data.get("message_id", 1),
        "date": 0,
        "chat": {"id": data.get("chat_id", TEST_CHAT_ID), "type": "supergroup"},
        "text": data.get("text", ""),
    }


def _make_api_router(calls: list):

    async def router(*, endpoint: str, data=None, **_kw):
        data = _parse_request_data(data)
        calls.append((endpoint, data))

        if endpoint == "getMe":
            return _GET_ME_RESPONSE
        if endpoint == "getChat":
            return _GET_CHAT_RESPONSE
        if endpoint in ("sendMessage", "sendPhoto", "sendDocument"):
            return _make_message_response(data, msg_id=_bump_message_id())
        if endpoint in ("editMessageText", "editMessageReplyMarkup"):
            return _make_message_response(data)
        if endpoint == "createForumTopic":
            return {
                "message_thread_id": _bump_message_id(),
                "name": data.get("name", "test-topic"),
                "icon_color": 0,
            }
        if endpoint in _PASSTHROUGH_ENDPOINTS:
            return True
        return True

    return router


@pytest.fixture
async def e2e_app(e2e_state_dir, e2e_tmux, intercepted_calls, monkeypatch):
    from ccgram.session import SessionManager

    fresh_manager = SessionManager()

    _sm_modules = [
        "ccgram.session",
        "ccgram.bot",
        "ccgram.handlers.text.text_handler",
        "ccgram.handlers.topics.directory_callbacks",
        "ccgram.handlers.topics.directory_browser",
        "ccgram.handlers.polling.polling_coordinator",
        "ccgram.handlers.messaging_pipeline.message_queue",
        "ccgram.handlers.recovery.recovery_callbacks",
        "ccgram.handlers.sessions_dashboard",
        "ccgram.handlers.live.screenshot_callbacks",
        "ccgram.handlers.recovery.history",
        "ccgram.handlers.hook_events",
        "ccgram.handlers.file_handler",
        "ccgram.handlers.voice.voice_callbacks",
        "ccgram.handlers.topics.window_callbacks",
        "ccgram.handlers.recovery.restore_command",
        "ccgram.handlers.recovery.resume_command",
        "ccgram.handlers.sync_command",
        "ccgram.handlers.commands.forward",
        "ccgram.handlers.topics.topic_orchestration",
        "ccgram.handlers.shell.shell_commands",
    ]
    import importlib

    for mod_name in _sm_modules:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "session_manager"):
            monkeypatch.setattr(mod, "session_manager", fresh_manager)

    from ccgram.bot import create_bot

    app = create_bot()

    router = _make_api_router(intercepted_calls)

    with patch.object(type(app.bot), "_do_post", side_effect=router):
        async with app:
            if app.post_init:
                await app.post_init(app)
            yield app, intercepted_calls, e2e_tmux, fresh_manager
            if app.post_stop:
                await app.post_stop(app)
            if app.post_shutdown:
                await app.post_shutdown(app)


@pytest.fixture
def work_dir(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# E2E Test Project\n\nThis is a test project for E2E tests.\n")
    return tmp_path
