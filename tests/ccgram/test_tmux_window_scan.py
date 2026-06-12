"""Tests for bounded tmux window scanning."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ccgram.tmux_manager import TmuxManager, TmuxWindow


def _make_proc(stdout: str = "", returncode: int = 0) -> AsyncMock:
    proc = AsyncMock()
    proc.communicate.return_value = (stdout.encode(), b"")
    proc.returncode = returncode
    return proc


@pytest.fixture
def manager() -> TmuxManager:
    tmux = TmuxManager.__new__(TmuxManager)
    tmux.session_name = "ccgram"
    return tmux


class TestCapturePane:
    async def test_plain_capture_uses_bounded_tmux_subprocess(
        self, manager: TmuxManager
    ) -> None:
        proc = _make_proc("hello\n")

        with patch(
            "ccgram.tmux_manager.asyncio.create_subprocess_exec", return_value=proc
        ) as mock_exec:
            text = await manager.capture_pane("@1")

        assert text == "hello"
        assert mock_exec.call_args.args == (
            "tmux",
            "capture-pane",
            "-p",
            "-t",
            "@1",
        )

    async def test_capture_timeout_backs_off_per_window(
        self, manager: TmuxManager
    ) -> None:
        with patch(
            "ccgram.tmux_manager.asyncio.create_subprocess_exec",
            side_effect=TimeoutError,
        ) as mock_exec:
            assert await manager.capture_pane("@1") is None
            assert await manager.capture_pane("@1") is None

        mock_exec.assert_called_once()


class TestListWindows:
    async def test_uses_bounded_list_windows_without_libtmux_session_scan(
        self, manager: TmuxManager
    ) -> None:
        proc = _make_proc(
            "@0\t__main__\t/home/u\tfish\t/dev/pts/1\t80\t24\n"
            "@1\tproj\t/home/u/proj\tpi\t/dev/pts/2\t120\t40\n"
            "@2\t_hidden\t/home/u\tbash\t/dev/pts/3\t80\t24\n"
            "@self\tself\t/home/u\tccgram\t/dev/pts/4\t80\t24\n"
        )

        with (
            patch(
                "ccgram.tmux_manager.asyncio.create_subprocess_exec", return_value=proc
            ) as mock_exec,
            patch("ccgram.tmux_manager.config") as mock_config,
        ):
            mock_config.tmux_main_window_name = "__main__"
            mock_config.own_window_id = "@self"
            windows = await manager.list_windows()

        assert [window.window_id for window in windows] == ["@1"]
        assert windows[0].window_name == "proj"
        assert windows[0].cwd == "/home/u/proj"
        assert windows[0].pane_current_command == "pi"
        assert windows[0].pane_tty == "/dev/pts/2"
        assert windows[0].pane_width == 120
        assert windows[0].pane_height == 40
        command = mock_exec.call_args.args
        assert command[:4] == ("tmux", "list-windows", "-t", "ccgram")
        assert "list-sessions" not in command

    async def test_cache_coalesces_repeated_scans(self, manager: TmuxManager) -> None:
        proc = _make_proc("@1\tproj\t/home/u/proj\tpi\t/dev/pts/2\t120\t40\n")

        with (
            patch(
                "ccgram.tmux_manager.asyncio.create_subprocess_exec", return_value=proc
            ) as mock_exec,
            patch("ccgram.tmux_manager.config") as mock_config,
        ):
            mock_config.tmux_main_window_name = "__main__"
            mock_config.own_window_id = ""
            first = await manager.list_windows()
            second = await manager.list_windows()

        assert first == second
        mock_exec.assert_called_once()

    async def test_filter_changes_bypass_cache(self, manager: TmuxManager) -> None:
        proc = _make_proc("@1\tproj\t/home/u/proj\tpi\t/dev/pts/2\t120\t40\n")

        with (
            patch(
                "ccgram.tmux_manager.asyncio.create_subprocess_exec", return_value=proc
            ) as mock_exec,
            patch("ccgram.tmux_manager.config") as mock_config,
        ):
            mock_config.tmux_main_window_name = "__main__"
            mock_config.own_window_id = ""
            assert [window.window_id for window in await manager.list_windows()] == [
                "@1"
            ]
            mock_config.own_window_id = "@1"
            assert await manager.list_windows() == []

        assert mock_exec.call_count == 2

    async def test_concurrent_cold_scans_share_one_tmux_client(
        self, manager: TmuxManager
    ) -> None:
        scan_started = asyncio.Event()
        release_scan = asyncio.Event()
        proc = _make_proc()

        async def communicate() -> tuple[bytes, bytes]:
            scan_started.set()
            await release_scan.wait()
            return (b"@1\tproj\t/home/u/proj\tpi\t/dev/pts/2\t120\t40\n", b"")

        proc.communicate.side_effect = communicate

        with (
            patch(
                "ccgram.tmux_manager.asyncio.create_subprocess_exec", return_value=proc
            ) as mock_exec,
            patch("ccgram.tmux_manager.config") as mock_config,
        ):
            mock_config.tmux_main_window_name = "__main__"
            mock_config.own_window_id = ""
            first_task = asyncio.create_task(manager.list_windows())
            await scan_started.wait()
            second_task = asyncio.create_task(manager.list_windows())
            await asyncio.sleep(0)
            assert not second_task.done()
            release_scan.set()
            first, second = await asyncio.gather(first_task, second_task)

        assert [window.window_id for window in first] == ["@1"]
        assert first == second
        mock_exec.assert_called_once()

    async def test_timeout_kills_client_and_backs_off(
        self, manager: TmuxManager
    ) -> None:
        proc = _make_proc()
        proc.communicate.side_effect = TimeoutError
        proc.returncode = None
        proc.kill = Mock()
        proc.wait = AsyncMock()

        with (
            patch(
                "ccgram.tmux_manager.asyncio.create_subprocess_exec", return_value=proc
            ) as mock_exec,
            patch("ccgram.tmux_manager.config") as mock_config,
        ):
            mock_config.tmux_main_window_name = "__main__"
            mock_config.own_window_id = ""
            assert await manager.list_windows() == []
            assert await manager.list_windows() == []

        mock_exec.assert_called_once()
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()
        assert manager._windows_backoff_expires > asyncio.get_running_loop().time()

    async def test_backoff_returns_cached_windows(self, manager: TmuxManager) -> None:
        cached = TmuxWindow(
            window_id="@1",
            window_name="proj",
            cwd="/tmp",
            pane_current_command="pi",
        )
        manager._windows_cache = [cached]
        manager._windows_cache_expires = 0.0
        manager._windows_backoff_expires = asyncio.get_running_loop().time() + 100
        manager._windows_query_lock = asyncio.Lock()

        with patch("ccgram.tmux_manager.asyncio.create_subprocess_exec") as mock_exec:
            result = await manager.list_windows()

        assert result == [cached]
        mock_exec.assert_not_called()
