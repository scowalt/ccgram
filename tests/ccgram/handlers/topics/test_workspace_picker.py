from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from ccgram.handlers.callback_data import CB_WS_SELECT, CB_WS_SKIP
from ccgram.handlers.topics.directory_callbacks import (
    _handle_workspace_callback,
    _show_workspace_picker_or_provider,
)
from ccgram.handlers.topics.window_launch_service import WindowLaunchRequest
from ccgram.handlers.user_state import (
    PENDING_THREAD_ID,
    PENDING_WORKSPACE_ID,
    PENDING_WORKSPACES,
)
from ccgram.multiplexer.base import MultiplexerCapabilities, WorkspaceRef
from ccgram.handlers.topics.directory_browser import BROWSE_PATH_KEY


def _capabilities(native_agent_status: bool) -> MultiplexerCapabilities:
    return MultiplexerCapabilities(
        name="herdr" if native_agent_status else "tmux",
        ids_stable_across_restart=not native_agent_status,
        exposes_pane_tty=not native_agent_status,
        native_agent_status=native_agent_status,
        read_max_lines=1000 if native_agent_status else None,
        self_identify_env="HERDR_PANE_ID" if native_agent_status else "TMUX_PANE",
        supports_event_stream=native_agent_status,
        native_worktrees=native_agent_status,
    )


def _make_query() -> AsyncMock:
    query = AsyncMock()
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.chat.type = "supergroup"
    query.message.chat.id = -100999
    return query


def _make_context(user_data: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = user_data if user_data is not None else {}
    ctx.bot = AsyncMock()
    ctx.bot.edit_forum_topic = AsyncMock()
    return ctx


def _make_update(thread_id: int = 42) -> MagicMock:
    update = MagicMock()
    update.message = None
    update.callback_query = MagicMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.message_thread_id = thread_id
    return update


_WORKSPACES = [
    WorkspaceRef(workspace_id="ws1", label="my-project", cwd="/home/user/project"),
    WorkspaceRef(workspace_id="ws2", label="other", cwd="/home/user/other"),
]


class TestShowWorkspacePickerOrProvider:
    @patch(
        "ccgram.handlers.topics.workspace_callbacks.safe_edit", new_callable=AsyncMock
    )
    @patch("ccgram.handlers.topics.workspace_callbacks.tmux_manager")
    async def test_herdr_with_workspaces_shows_picker(
        self, mock_mux: MagicMock, mock_edit: AsyncMock, tmp_path: Path
    ) -> None:
        mock_mux.capabilities = _capabilities(native_agent_status=True)
        mock_mux.list_workspaces = AsyncMock(return_value=_WORKSPACES)

        user_data: dict = {}
        context = _make_context(user_data)
        await _show_workspace_picker_or_provider(_make_query(), str(tmp_path), context)

        text = mock_edit.call_args[0][1]
        assert "Select Workspace" in text
        assert user_data[PENDING_WORKSPACES] == [
            ("ws1", "my-project", "/home/user/project"),
            ("ws2", "other", "/home/user/other"),
        ]

    @patch(
        "ccgram.handlers.topics.workspace_callbacks.safe_edit", new_callable=AsyncMock
    )
    @patch("ccgram.handlers.topics.workspace_callbacks.tmux_manager")
    async def test_herdr_empty_workspaces_shows_provider_picker(
        self, mock_mux: MagicMock, mock_edit: AsyncMock, tmp_path: Path
    ) -> None:
        mock_mux.capabilities = _capabilities(native_agent_status=True)
        mock_mux.list_workspaces = AsyncMock(return_value=[])

        user_data = {
            PENDING_WORKSPACE_ID: "stale",
            PENDING_WORKSPACES: [("old", "", "")],
        }
        context = _make_context(user_data)
        await _show_workspace_picker_or_provider(_make_query(), str(tmp_path), context)

        text = mock_edit.call_args[0][1]
        assert "Select Provider" in text
        assert PENDING_WORKSPACE_ID not in user_data
        assert PENDING_WORKSPACES not in user_data

    @patch(
        "ccgram.handlers.topics.workspace_callbacks.safe_edit", new_callable=AsyncMock
    )
    @patch("ccgram.handlers.topics.workspace_callbacks.tmux_manager")
    async def test_tmux_skips_workspace_picker(
        self, mock_mux: MagicMock, mock_edit: AsyncMock, tmp_path: Path
    ) -> None:
        mock_mux.capabilities = _capabilities(native_agent_status=False)

        context = _make_context()
        await _show_workspace_picker_or_provider(_make_query(), str(tmp_path), context)

        text = mock_edit.call_args[0][1]
        assert "Select Provider" in text
        mock_mux.list_workspaces.assert_not_called()


class TestHandleWorkspaceCallback:
    @patch(
        "ccgram.handlers.topics.workspace_callbacks.safe_edit", new_callable=AsyncMock
    )
    async def test_select_stores_workspace_id_and_shows_provider(
        self, mock_edit: AsyncMock, tmp_path: Path
    ) -> None:
        ws_triples = [
            ("ws1", "my-project", "/home/user/project"),
            ("ws2", "other", "/home/user/other"),
        ]
        user_data = {
            PENDING_THREAD_ID: 42,
            BROWSE_PATH_KEY: str(tmp_path),
            PENDING_WORKSPACES: ws_triples,
        }
        context = _make_context(user_data)

        await _handle_workspace_callback(
            _make_query(), f"{CB_WS_SELECT}1", _make_update(42), context
        )

        assert user_data[PENDING_WORKSPACE_ID] == "ws2"
        text = mock_edit.call_args[0][1]
        assert "Select Provider" in text

    @patch(
        "ccgram.handlers.topics.workspace_callbacks.safe_edit", new_callable=AsyncMock
    )
    async def test_skip_clears_workspace_id_and_shows_provider(
        self, mock_edit: AsyncMock, tmp_path: Path
    ) -> None:
        user_data = {
            PENDING_THREAD_ID: 42,
            BROWSE_PATH_KEY: str(tmp_path),
            PENDING_WORKSPACE_ID: "stale-ws",
            PENDING_WORKSPACES: [("ws1", "label", "/cwd")],
        }
        context = _make_context(user_data)

        await _handle_workspace_callback(
            _make_query(), CB_WS_SKIP, _make_update(42), context
        )

        assert PENDING_WORKSPACE_ID not in user_data
        assert PENDING_WORKSPACES not in user_data
        text = mock_edit.call_args[0][1]
        assert "Select Provider" in text

    async def test_stale_flow_reset_fails_closed(self, tmp_path: Path) -> None:
        query = _make_query()
        context = _make_context({BROWSE_PATH_KEY: str(tmp_path)})

        await _handle_workspace_callback(query, CB_WS_SKIP, _make_update(42), context)

        query.answer.assert_awaited_once()
        call_kwargs = query.answer.call_args[1]
        assert call_kwargs.get("show_alert") is True

    async def test_topic_mismatch_fails_closed(self, tmp_path: Path) -> None:
        query = _make_query()
        user_data = {PENDING_THREAD_ID: 99, BROWSE_PATH_KEY: str(tmp_path)}
        context = _make_context(user_data)

        await _handle_workspace_callback(
            query,
            CB_WS_SKIP,
            _make_update(42),
            context,
        )

        query.answer.assert_awaited_once()
        assert query.answer.call_args[1].get("show_alert") is True

    @patch(
        "ccgram.handlers.topics.workspace_callbacks.safe_edit", new_callable=AsyncMock
    )
    async def test_out_of_range_index_shows_error(
        self, mock_edit: AsyncMock, tmp_path: Path
    ) -> None:
        user_data = {
            PENDING_THREAD_ID: 42,
            BROWSE_PATH_KEY: str(tmp_path),
            PENDING_WORKSPACES: [("ws1", "label", "/cwd")],
        }
        context = _make_context(user_data)

        await _handle_workspace_callback(
            _make_query(), f"{CB_WS_SELECT}5", _make_update(42), context
        )

        text = mock_edit.call_args[0][1]
        assert "❌" in text


class TestWorkspaceIdThreaded:
    @patch(
        "ccgram.handlers.topics.window_launch_service.safe_edit", new_callable=AsyncMock
    )
    @patch("ccgram.handlers.topics.window_launch_service.tmux_manager")
    @patch("ccgram.handlers.topics.window_launch_service.session_manager")
    @patch("ccgram.handlers.topics.window_launch_service.thread_router")
    @patch("ccgram.handlers.topics.window_launch_service.topic_orchestration")
    @patch("ccgram.handlers.topics.window_launch_service.user_preferences")
    @patch("ccgram.handlers.topics.window_launch_service.session_map_sync")
    async def test_chosen_workspace_id_passed_to_create_window(
        self,
        mock_sms: MagicMock,
        mock_prefs: MagicMock,
        mock_orch: MagicMock,
        mock_tr: MagicMock,
        mock_sm: MagicMock,
        mock_mux: MagicMock,
        mock_edit: AsyncMock,
        tmp_path: Path,
    ) -> None:
        from ccgram.handlers.topics.directory_callbacks import _create_window_and_bind

        mock_mux.create_window = AsyncMock(return_value=(True, "ok", "my-tab", "w1:t1"))
        mock_mux.stamp_pane_title = AsyncMock()
        mock_mux.capabilities.native_worktrees = False
        mock_tr.get_window_for_thread.return_value = None
        mock_tr.resolve_chat_id.return_value = -100999
        mock_sm.set_window_provider = MagicMock()
        mock_sm.set_window_origin = MagicMock()
        mock_sm.set_window_cwd = MagicMock()
        mock_sm.set_window_approval_mode = MagicMock()
        mock_sms.wait_for_session_map_entry = AsyncMock()

        with (
            patch("ccgram.providers.resolve_launch_command", return_value="claude"),
            patch(
                "ccgram.handlers.topics.window_launch_service.provider_registry"
            ) as mock_reg,
        ):
            mock_caps = MagicMock()
            mock_caps.chat_first_command_path = False
            mock_caps.has_yolo_confirmation = False
            mock_caps.supports_hook = False
            mock_reg.get.return_value.capabilities = mock_caps

            user_data = {
                PENDING_THREAD_ID: 42,
                PENDING_WORKSPACE_ID: "ws-chosen",
            }
            query = _make_query()
            context = _make_context(user_data)

            await _create_window_and_bind(
                query,
                context,
                WindowLaunchRequest(
                    user_id=100,
                    thread_id=user_data.get(PENDING_THREAD_ID),
                    provider_name="claude",
                    cwd=str(tmp_path),
                    mode="normal",
                    pending_text=None,
                ),
            )

        mock_mux.create_window.assert_awaited_once()
        call_kwargs = mock_mux.create_window.call_args[1]
        assert call_kwargs.get("workspace_id") == "ws-chosen"
        assert PENDING_WORKSPACE_ID not in user_data

    @patch(
        "ccgram.handlers.topics.window_launch_service.safe_edit", new_callable=AsyncMock
    )
    @patch("ccgram.handlers.topics.window_launch_service.tmux_manager")
    @patch("ccgram.handlers.topics.window_launch_service.session_manager")
    @patch("ccgram.handlers.topics.window_launch_service.thread_router")
    @patch("ccgram.handlers.topics.window_launch_service.topic_orchestration")
    @patch("ccgram.handlers.topics.window_launch_service.user_preferences")
    @patch("ccgram.handlers.topics.window_launch_service.session_map_sync")
    async def test_no_workspace_id_passes_none(
        self,
        mock_sms: MagicMock,
        mock_prefs: MagicMock,
        mock_orch: MagicMock,
        mock_tr: MagicMock,
        mock_sm: MagicMock,
        mock_mux: MagicMock,
        mock_edit: AsyncMock,
        tmp_path: Path,
    ) -> None:
        from ccgram.handlers.topics.directory_callbacks import _create_window_and_bind

        mock_mux.create_window = AsyncMock(return_value=(True, "ok", "my-tab", "w1:t1"))
        mock_mux.stamp_pane_title = AsyncMock()
        mock_mux.capabilities.native_worktrees = False
        mock_tr.get_window_for_thread.return_value = None
        mock_tr.resolve_chat_id.return_value = -100999
        mock_sm.set_window_provider = MagicMock()
        mock_sm.set_window_origin = MagicMock()
        mock_sm.set_window_cwd = MagicMock()
        mock_sm.set_window_approval_mode = MagicMock()
        mock_sms.wait_for_session_map_entry = AsyncMock()

        with (
            patch("ccgram.providers.resolve_launch_command", return_value="claude"),
            patch(
                "ccgram.handlers.topics.window_launch_service.provider_registry"
            ) as mock_reg,
        ):
            mock_caps = MagicMock()
            mock_caps.chat_first_command_path = False
            mock_caps.has_yolo_confirmation = False
            mock_caps.supports_hook = False
            mock_reg.get.return_value.capabilities = mock_caps

            user_data = {PENDING_THREAD_ID: 42}
            query = _make_query()
            context = _make_context(user_data)

            await _create_window_and_bind(
                query,
                context,
                WindowLaunchRequest(
                    user_id=100,
                    thread_id=user_data.get(PENDING_THREAD_ID),
                    provider_name="claude",
                    cwd=str(tmp_path),
                    mode="normal",
                    pending_text=None,
                ),
            )

        call_kwargs = mock_mux.create_window.call_args[1]
        assert call_kwargs.get("workspace_id") is None
