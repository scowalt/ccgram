"""Tests for resilient Telegram polling requests."""

from pathlib import Path
import tomllib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import NetworkError, TimedOut
from telegram.request import HTTPXRequest

from ccgram.bot import create_bot
from ccgram.telegram_request import (
    ResilientPollingHTTPXRequest,
    clear_polling_transport_failure,
    polling_transport_failed_recently,
)


class TestResilientPollingHTTPXRequest:
    async def test_rebuilds_client_after_timeout(self) -> None:
        request = ResilientPollingHTTPXRequest()
        old_client = request._client

        with (
            patch.object(
                HTTPXRequest,
                "do_request",
                AsyncMock(side_effect=TimedOut("pool timeout")),
            ),
            pytest.raises(TimedOut),
        ):
            await request.do_request("https://example.com", "POST")

        assert request._client is not old_client
        assert old_client.is_closed
        assert not request._client.is_closed

    async def test_rebuilds_client_after_network_error(self) -> None:
        request = ResilientPollingHTTPXRequest()
        old_client = request._client

        with (
            patch.object(
                HTTPXRequest,
                "do_request",
                AsyncMock(side_effect=NetworkError("proxy broken")),
            ),
            pytest.raises(NetworkError),
        ):
            await request.do_request("https://example.com", "POST")

        assert request._client is not old_client
        assert old_client.is_closed
        assert not request._client.is_closed


def _reset_log_calls(mock_logger, level: str) -> list:
    return [
        c
        for c in getattr(mock_logger, level).call_args_list
        if c.args and "Reset Telegram polling" in c.args[0]
    ]


class TestPollingTransportFailureTracking:
    async def test_tracking_request_marks_recent_polling_failure(self) -> None:
        clear_polling_transport_failure()
        request = ResilientPollingHTTPXRequest(track_polling_failures=True)

        with (
            patch.object(
                HTTPXRequest,
                "do_request",
                AsyncMock(side_effect=TimedOut("pool timeout")),
            ),
            pytest.raises(TimedOut),
        ):
            await request.do_request("https://example.com", "POST")

        assert polling_transport_failed_recently(within_seconds=999)
        clear_polling_transport_failure()

    async def test_untracked_request_does_not_mark_polling_failure(self) -> None:
        clear_polling_transport_failure()
        request = ResilientPollingHTTPXRequest()

        with (
            patch.object(
                HTTPXRequest,
                "do_request",
                AsyncMock(side_effect=TimedOut("pool timeout")),
            ),
            pytest.raises(TimedOut),
        ):
            await request.do_request("https://example.com", "POST")

        assert not polling_transport_failed_recently(within_seconds=999)

    async def test_tracking_request_success_clears_polling_failure(self) -> None:
        clear_polling_transport_failure()
        request = ResilientPollingHTTPXRequest(track_polling_failures=True)
        sentinel = object()
        mock = AsyncMock(side_effect=[TimedOut("pool timeout"), sentinel])

        with patch.object(HTTPXRequest, "do_request", mock):
            with pytest.raises(TimedOut):
                await request.do_request("https://example.com", "POST")
            assert polling_transport_failed_recently(within_seconds=999)
            await request.do_request("https://example.com", "POST")

        assert not polling_transport_failed_recently(within_seconds=999)


class TestResetWarningRateLimit:
    async def test_first_reset_warns(self) -> None:
        request = ResilientPollingHTTPXRequest()
        with (
            patch.object(
                HTTPXRequest,
                "do_request",
                AsyncMock(side_effect=TimedOut("t")),
            ),
            patch("ccgram.telegram_request.logger") as mock_logger,
            pytest.raises(TimedOut),
        ):
            await request.do_request("https://example.com", "POST")
        assert len(_reset_log_calls(mock_logger, "warning")) == 1
        assert _reset_log_calls(mock_logger, "debug") == []

    async def test_repeated_resets_within_interval_demoted_to_debug(self) -> None:
        request = ResilientPollingHTTPXRequest()
        with (
            patch.object(
                HTTPXRequest,
                "do_request",
                AsyncMock(side_effect=TimedOut("t")),
            ),
            patch("ccgram.telegram_request.logger") as mock_logger,
        ):
            for _ in range(5):
                with pytest.raises(TimedOut):
                    await request.do_request("https://example.com", "POST")

        assert len(_reset_log_calls(mock_logger, "warning")) == 1
        assert len(_reset_log_calls(mock_logger, "debug")) == 4

    async def test_success_resets_warn_eligibility(self) -> None:
        request = ResilientPollingHTTPXRequest()
        sentinel = object()
        mock = AsyncMock(side_effect=[TimedOut("t"), sentinel, TimedOut("t")])

        with (
            patch.object(HTTPXRequest, "do_request", mock),
            patch("ccgram.telegram_request.logger") as mock_logger,
        ):
            with pytest.raises(TimedOut):
                await request.do_request("u", "POST")
            await request.do_request("u", "POST")
            with pytest.raises(TimedOut):
                await request.do_request("u", "POST")

        assert len(_reset_log_calls(mock_logger, "warning")) == 2


class TestCreateBotPollingRequest:
    @patch("ccgram.bot.config")
    def test_uses_resilient_request_for_telegram_traffic(
        self, mock_config: MagicMock
    ) -> None:
        mock_config.telegram_bot_token = "fake:token"

        app = create_bot()

        assert isinstance(app.bot._request[0], ResilientPollingHTTPXRequest)
        assert isinstance(app.bot._request[1], ResilientPollingHTTPXRequest)
        assert app.bot._request[0]._client._transport._pool._max_connections == 1
        assert app.bot._request[1]._client._transport._pool._max_connections == 256
        assert app.bot._request[0]._track_polling_failures is True
        assert app.bot._request[1]._track_polling_failures is False


class TestProjectDependencies:
    def test_declares_ptb_socks_support(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        pyproject = tomllib.loads((project_root / "pyproject.toml").read_text())
        dependencies = pyproject["project"]["dependencies"]

        assert any(
            dependency.startswith("python-telegram-bot[")
            and "socks" in dependency.partition("[")[2].partition("]")[0].split(",")
            for dependency in dependencies
        )
