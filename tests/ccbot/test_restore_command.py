"""Tests for /restore command — dead topic recovery."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.handlers.restore_command import restore_command
from ccbot.session import WindowState


@pytest.fixture(autouse=True)
def _patch_deps():
    with (
        patch("ccbot.handlers.restore_command.session_manager") as mock_sm,
        patch("ccbot.handlers.restore_command.tmux_manager") as mock_tm,
        patch("ccbot.handlers.restore_command.config") as mock_cfg,
        patch("ccbot.handlers.restore_command.build_recovery_keyboard") as mock_kb,
    ):
        mock_cfg.is_user_allowed.return_value = True
        mock_sm.resolve_window_for_thread.return_value = None
        mock_tm.find_window_by_id = AsyncMock(return_value=None)
        mock_kb.return_value = MagicMock()
        yield mock_sm, mock_tm, mock_cfg, mock_kb


def _make_update(*, user_id=100, thread_id=42):  # noqa: ANN001
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id)
    update.message = AsyncMock()
    update.message.message_thread_id = thread_id
    return update


class TestRestoreCommand:
    async def test_no_user_returns_early(self, _patch_deps) -> None:
        update = MagicMock()
        update.effective_user = None
        update.message = AsyncMock()

        with patch("ccbot.handlers.restore_command.safe_reply") as mock_reply:
            await restore_command(update, MagicMock())
            mock_reply.assert_not_called()

    async def test_no_message_returns_early(self, _patch_deps) -> None:
        update = MagicMock()
        update.effective_user = MagicMock(id=100)
        update.message = None

        with patch("ccbot.handlers.restore_command.safe_reply") as mock_reply:
            await restore_command(update, MagicMock())
            mock_reply.assert_not_called()

    async def test_unauthorized_user_rejected(self, _patch_deps) -> None:
        _, _, mock_cfg, _ = _patch_deps
        mock_cfg.is_user_allowed.return_value = False
        update = _make_update()

        with patch("ccbot.handlers.restore_command.safe_reply") as mock_reply:
            await restore_command(update, MagicMock())
            mock_reply.assert_called_once()
            assert "not authorized" in mock_reply.call_args[0][1]

    async def test_no_thread_id(self, _patch_deps) -> None:
        update = _make_update(thread_id=None)

        with patch("ccbot.handlers.restore_command.safe_reply") as mock_reply:
            await restore_command(update, MagicMock())
            assert "inside a topic" in mock_reply.call_args[0][1]

    async def test_unbound_topic(self, _patch_deps) -> None:
        mock_sm, _, _, _ = _patch_deps
        mock_sm.resolve_window_for_thread.return_value = None
        update = _make_update()

        with patch("ccbot.handlers.restore_command.safe_reply") as mock_reply:
            await restore_command(update, MagicMock())
            assert "No session bound" in mock_reply.call_args[0][1]

    async def test_alive_window(self, _patch_deps) -> None:
        mock_sm, mock_tm, _, _ = _patch_deps
        mock_sm.resolve_window_for_thread.return_value = "@5"
        mock_tm.find_window_by_id.return_value = MagicMock()
        update = _make_update()

        with patch("ccbot.handlers.restore_command.safe_reply") as mock_reply:
            await restore_command(update, MagicMock())
            assert "still running" in mock_reply.call_args[0][1]

    async def test_dead_window_no_cwd(self, _patch_deps) -> None:
        mock_sm, mock_tm, _, _ = _patch_deps
        mock_sm.resolve_window_for_thread.return_value = "@5"
        mock_tm.find_window_by_id.return_value = None
        mock_sm.get_window_state.return_value = WindowState()
        update = _make_update()

        with patch("ccbot.handlers.restore_command.safe_reply") as mock_reply:
            await restore_command(update, MagicMock())
            assert "Directory no longer exists" in mock_reply.call_args[0][1]

    async def test_dead_window_nonexistent_cwd(self, _patch_deps) -> None:
        mock_sm, mock_tm, _, _ = _patch_deps
        mock_sm.resolve_window_for_thread.return_value = "@5"
        mock_tm.find_window_by_id.return_value = None
        mock_sm.get_window_state.return_value = WindowState(cwd="/nonexistent/path")
        update = _make_update()

        with patch("ccbot.handlers.restore_command.safe_reply") as mock_reply:
            await restore_command(update, MagicMock())
            assert "Directory no longer exists" in mock_reply.call_args[0][1]

    async def test_dead_window_shows_recovery_keyboard(
        self, _patch_deps, tmp_path
    ) -> None:
        mock_sm, mock_tm, _, mock_kb = _patch_deps
        mock_sm.resolve_window_for_thread.return_value = "@5"
        mock_tm.find_window_by_id.return_value = None
        mock_sm.get_window_state.return_value = WindowState(cwd=str(tmp_path))
        mock_sm.get_display_name.return_value = "my-project"
        update = _make_update()

        with patch("ccbot.handlers.restore_command.safe_reply") as mock_reply:
            await restore_command(update, MagicMock())
            mock_kb.assert_called_once_with("@5")
            mock_reply.assert_called_once()
            text = mock_reply.call_args[0][1]
            assert "my-project" in text
            assert "How would you like to recover?" in text
            assert mock_reply.call_args.kwargs["reply_markup"] == mock_kb.return_value
