import asyncio
import time

import httpx
import structlog
from telegram.error import NetworkError, TimedOut
from telegram.request import HTTPXRequest

logger = structlog.get_logger()

_RESET_WARN_INTERVAL_S: float = 30.0
_POLLING_TRANSPORT_FAILURE_GRACE_S: float = 30.0
_last_polling_transport_failure_ts: float | None = None
_last_transport_failure_ts: float | None = None


def _recent(timestamp: float | None, *, within_seconds: float) -> bool:
    if timestamp is None:
        return False
    return time.monotonic() - timestamp <= within_seconds


def polling_transport_failed_recently(
    *, within_seconds: float = _POLLING_TRANSPORT_FAILURE_GRACE_S
) -> bool:
    return _recent(_last_polling_transport_failure_ts, within_seconds=within_seconds)


def telegram_transport_failed_recently(
    *, within_seconds: float = _POLLING_TRANSPORT_FAILURE_GRACE_S
) -> bool:
    return _recent(_last_transport_failure_ts, within_seconds=within_seconds)


def clear_polling_transport_failure() -> None:
    global _last_polling_transport_failure_ts
    _last_polling_transport_failure_ts = None


def clear_telegram_transport_failure() -> None:
    global _last_transport_failure_ts
    _last_transport_failure_ts = None


def _mark_polling_transport_failure() -> None:
    global _last_polling_transport_failure_ts
    _last_polling_transport_failure_ts = time.monotonic()


def _mark_transport_failure() -> None:
    global _last_transport_failure_ts
    _last_transport_failure_ts = time.monotonic()


class ResilientPollingHTTPXRequest(HTTPXRequest):
    def __init__(
        self,
        *args,
        track_polling_failures: bool = False,
        **kwargs,
    ) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._last_reset_warn_ts: float = 0.0
        self._track_polling_failures = track_polling_failures

    async def _reset_client(self, *, reason: str) -> None:
        old_client = self._client
        self._client = self._build_client()

        try:
            async with asyncio.timeout(1.0):
                await old_client.aclose()
        except (TimeoutError, RuntimeError, OSError, httpx.HTTPError) as exc:
            logger.debug(
                "Ignoring error while closing stale polling client after %s: %s",
                reason,
                exc,
            )

    def _should_warn_for_reset(self, now: float) -> bool:
        if now - self._last_reset_warn_ts >= _RESET_WARN_INTERVAL_S:
            self._last_reset_warn_ts = now
            return True
        return False

    async def do_request(self, *args, **kwargs):  # type: ignore[override]
        try:
            result = await super().do_request(*args, **kwargs)
        except (TimedOut, NetworkError) as exc:
            await self._reset_client(reason=exc.__class__.__name__)
            _mark_transport_failure()
            if self._track_polling_failures:
                _mark_polling_transport_failure()
            log = (
                logger.warning
                if self._should_warn_for_reset(time.monotonic())
                else logger.debug
            )
            log(
                "Reset Telegram polling HTTP client after %s: %s",
                exc.__class__.__name__,
                exc,
            )
            raise
        else:
            self._last_reset_warn_ts = 0.0
            if self._track_polling_failures:
                clear_polling_transport_failure()
            return result
