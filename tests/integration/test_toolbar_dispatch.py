from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import Application, CallbackQueryHandler

from ccgram.handlers.callback_data import CB_TOOLBAR
from ccgram.handlers.callback_registry import (
    dispatch as callback_dispatch,
    load_handlers,
)
from ccgram.handlers.toolbar.toolbar_keyboard import (
    build_toolbar_keyboard,
    reload_toolbar_config,
)
from ccgram.toolbar_config import (
    BUILTIN_ACTIONS,
    DEFAULT_LAYOUTS,
    ToolbarAction,
    ToolbarConfig,
    ToolbarLayout,
)

pytestmark = pytest.mark.integration

TEST_USER_ID = 12345
TEST_CHAT_ID = -100999
TEST_THREAD_ID = 42
TEST_WINDOW_ID = "@5"


def _make_callback_query_update(callback_data: str, *, bot=None) -> Update:
    user = User(id=TEST_USER_ID, first_name="Test", is_bot=False)
    chat = Chat(id=TEST_CHAT_ID, type="supergroup")
    message = Message(
        message_id=1,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text="/toolbar",
        message_thread_id=TEST_THREAD_ID,
    )
    query = CallbackQuery(
        id="cb1",
        from_user=user,
        chat_instance="ci1",
        data=callback_data,
        message=message,
    )
    update = Update(update_id=1, callback_query=query)
    if bot:
        update.set_bot(bot)
        message.set_bot(bot)
        query.set_bot(bot)
    return update


@pytest.fixture(autouse=True)
def _reset_toolbar_config():
    reload_toolbar_config()
    yield
    reload_toolbar_config()


@pytest.fixture
async def app():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        pytest.skip("TELEGRAM_BOT_TOKEN not set")
    application = Application.builder().token(token).build()
    load_handlers()
    application.add_handler(CallbackQueryHandler(callback_dispatch))
    mock_post = AsyncMock(
        return_value={
            "id": 1,
            "first_name": "Bot",
            "is_bot": True,
            "username": "testbot",
        }
    )
    with patch.object(type(application.bot), "_do_post", mock_post):
        async with application:
            yield application


class TestKeyboardBuild:
    @pytest.mark.parametrize(
        "provider,expected_rows",
        [
            ("claude", 4),
            ("codex", 4),
            ("gemini", 4),
            ("pi", 4),
            ("shell", 3),
        ],
    )
    def test_default_keyboard_for_each_provider(
        self, provider: str, expected_rows: int
    ) -> None:
        kb = build_toolbar_keyboard(TEST_WINDOW_ID, provider)
        assert len(kb.inline_keyboard) == expected_rows
        for row in kb.inline_keyboard:
            assert 1 <= len(row) <= 8
            for btn in row:
                cb = btn.callback_data
                assert isinstance(cb, str)
                assert cb.startswith(CB_TOOLBAR)
                assert TEST_WINDOW_ID in cb

    def test_callback_data_under_64_bytes(self) -> None:
        kb = build_toolbar_keyboard("@99999", "claude")
        for row in kb.inline_keyboard:
            for btn in row:
                cb = btn.callback_data
                assert isinstance(cb, str)
                assert len(cb.encode("utf-8")) <= 64


@pytest.mark.usefixtures("app")
class TestDispatchRoundTrip:
    @pytest.mark.parametrize(
        ("action_name", "expected_key", "expected_literal"),
        [
            ("esc", "Escape", False),
            ("enter", "Enter", False),
            ("tab", "Tab", False),
            ("eof", "C-d", False),
            ("susp", "C-z", False),
            ("mode", "\x1b[Z", True),
        ],
    )
    async def test_key_action_dispatched_to_send_keys(
        self,
        app: Application,
        action_name: str,
        expected_key: str,
        expected_literal: bool,
    ) -> None:
        update = _make_callback_query_update(
            f"tb:{TEST_WINDOW_ID}:{action_name}", bot=app.bot
        )
        with (
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.user_owns_window",
                return_value=True,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.tmux_manager"
            ) as mock_tmux,
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.refresh_button_label",
                new=AsyncMock(return_value="Edit"),
            ),
            patch.object(
                CallbackQuery, "answer", new_callable=AsyncMock
            ) as mock_answer,
        ):
            mock_tmux.find_window_by_id = AsyncMock(
                return_value=MagicMock(window_id=TEST_WINDOW_ID)
            )
            mock_tmux.send_keys = AsyncMock()
            await app.process_update(update)
        mock_tmux.send_keys.assert_awaited_once_with(
            TEST_WINDOW_ID, expected_key, enter=False, literal=expected_literal
        )
        mock_answer.assert_awaited()

    async def test_text_action_uses_enter_and_literal(self, app: Application) -> None:
        clear = ToolbarAction(
            name="clear",
            emoji="\U0001f9f9",
            text="Clear",
            action_type="text",
            payload="/clear",
        )
        cfg = ToolbarConfig(
            layouts=dict(DEFAULT_LAYOUTS),
            actions={**BUILTIN_ACTIONS, "clear": clear},
        )
        update = _make_callback_query_update(f"tb:{TEST_WINDOW_ID}:clear", bot=app.bot)
        with (
            patch(
                "ccgram.handlers.toolbar.toolbar_keyboard.get_toolbar_config",
                return_value=cfg,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.get_toolbar_config",
                return_value=cfg,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.user_owns_window",
                return_value=True,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.tmux_manager"
            ) as mock_tmux,
            patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        ):
            mock_tmux.find_window_by_id = AsyncMock(
                return_value=MagicMock(window_id=TEST_WINDOW_ID)
            )
            mock_tmux.send_keys = AsyncMock()
            await app.process_update(update)
        mock_tmux.send_keys.assert_awaited_once_with(
            TEST_WINDOW_ID, "/clear", enter=True, literal=True
        )

    async def test_builtin_ctrlc_dispatched(self, app: Application) -> None:
        update = _make_callback_query_update(f"tb:{TEST_WINDOW_ID}:ctrlc", bot=app.bot)
        with (
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.user_owns_window",
                return_value=True,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.tmux_manager"
            ) as mock_tmux,
            patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        ):
            mock_tmux.find_window_by_id = AsyncMock(
                return_value=MagicMock(window_id=TEST_WINDOW_ID)
            )
            mock_tmux.send_keys = AsyncMock()
            await app.process_update(update)
        mock_tmux.send_keys.assert_awaited_once_with(
            TEST_WINDOW_ID, "C-c", enter=False, literal=False
        )

    async def test_builtin_dismiss_deletes_message(self, app: Application) -> None:
        update = _make_callback_query_update(f"tb:{TEST_WINDOW_ID}:close", bot=app.bot)
        with (
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.user_owns_window",
                return_value=True,
            ),
            patch.object(
                CallbackQuery, "delete_message", new_callable=AsyncMock
            ) as mock_delete,
            patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        ):
            await app.process_update(update)
        mock_delete.assert_awaited_once()

    async def test_unknown_action_alerts_user(self, app: Application) -> None:
        update = _make_callback_query_update(
            f"tb:{TEST_WINDOW_ID}:nothereyet", bot=app.bot
        )
        with (
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.user_owns_window",
                return_value=True,
            ),
            patch.object(
                CallbackQuery, "answer", new_callable=AsyncMock
            ) as mock_answer,
        ):
            await app.process_update(update)
        mock_answer.assert_awaited_once()
        args, kwargs = mock_answer.call_args
        assert "nothereyet" in args[0]
        assert kwargs.get("show_alert") is True


@pytest.mark.usefixtures("app")
class TestCustomConfigDispatch:
    async def test_custom_layout_renders_and_dispatches(self, app: Application) -> None:
        summary = ToolbarAction(
            name="summary",
            emoji="📝",
            text="Sum",
            action_type="text",
            payload="/summary",
        )
        custom_layout = ToolbarLayout(
            style="text",
            buttons=(("screen", "summary"), ("close",)),
        )
        cfg = ToolbarConfig(
            layouts={**DEFAULT_LAYOUTS, "claude": custom_layout},
            actions={**BUILTIN_ACTIONS, "summary": summary},
        )
        with patch(
            "ccgram.handlers.toolbar.toolbar_keyboard.get_toolbar_config",
            return_value=cfg,
        ):
            kb = build_toolbar_keyboard(TEST_WINDOW_ID, "claude")
        assert len(kb.inline_keyboard) == 2
        assert kb.inline_keyboard[0][0].text == "Screen"
        assert kb.inline_keyboard[0][1].text == "Sum"
        assert kb.inline_keyboard[1][0].text == "Close"

        update = _make_callback_query_update(
            f"tb:{TEST_WINDOW_ID}:summary", bot=app.bot
        )
        with (
            patch(
                "ccgram.handlers.toolbar.toolbar_keyboard.get_toolbar_config",
                return_value=cfg,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.get_toolbar_config",
                return_value=cfg,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.user_owns_window",
                return_value=True,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.tmux_manager"
            ) as mock_tmux,
            patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        ):
            mock_tmux.find_window_by_id = AsyncMock(
                return_value=MagicMock(window_id=TEST_WINDOW_ID)
            )
            mock_tmux.send_keys = AsyncMock()
            await app.process_update(update)
        mock_tmux.send_keys.assert_awaited_once_with(
            TEST_WINDOW_ID, "/summary", enter=True, literal=True
        )

    async def test_user_overrides_builtin_via_config(self, app: Application) -> None:
        custom_mode = ToolbarAction(
            name="mode",
            emoji="🆕",
            text="Mode",
            action_type="key",
            payload="C-x",
            literal=False,
            read_state=False,
        )
        cfg = ToolbarConfig(
            layouts=dict(DEFAULT_LAYOUTS),
            actions={**BUILTIN_ACTIONS, "mode": custom_mode},
        )
        update = _make_callback_query_update(f"tb:{TEST_WINDOW_ID}:mode", bot=app.bot)
        with (
            patch(
                "ccgram.handlers.toolbar.toolbar_keyboard.get_toolbar_config",
                return_value=cfg,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.get_toolbar_config",
                return_value=cfg,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.user_owns_window",
                return_value=True,
            ),
            patch(
                "ccgram.handlers.toolbar.toolbar_callbacks.tmux_manager"
            ) as mock_tmux,
            patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        ):
            mock_tmux.find_window_by_id = AsyncMock(
                return_value=MagicMock(window_id=TEST_WINDOW_ID)
            )
            mock_tmux.send_keys = AsyncMock()
            await app.process_update(update)
        mock_tmux.send_keys.assert_awaited_once_with(
            TEST_WINDOW_ID, "C-x", enter=False, literal=False
        )
