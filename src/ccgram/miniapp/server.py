from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from .api import register_terminal_routes, register_transcript_routes
from .auth import InvalidTokenError, verify_token

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any

    PaneCapture = Callable[[str], Awaitable[str | None]]
    PaneByIdCapture = Callable[[str, str], Awaitable[str | None]]
    PaneList = Callable[[str], Awaitable[list[dict[str, Any]]]]
    TranscriptReader = Callable[[str], Awaitable[list[dict[str, Any]]]]

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_INDEX_FILE = _STATIC_DIR / "index.html"
try:
    _INDEX_TEMPLATE: str | None = _INDEX_FILE.read_text(encoding="utf-8")
except OSError as _exc:
    logger.warning("miniapp index.html not loaded: %s", _exc)
    _INDEX_TEMPLATE = None

_BOT_TOKEN_KEY = web.AppKey("bot_token", str)


async def _handle_health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


def _render_index(payload_meta: str) -> str:
    assert _INDEX_TEMPLATE is not None
    return _INDEX_TEMPLATE.replace('<meta name="ccgram-payload-slot">', payload_meta)


async def _handle_app(request: web.Request) -> web.Response:
    token = request.match_info["token"]
    bot_token = request.app[_BOT_TOKEN_KEY]
    if _INDEX_TEMPLATE is None:
        return web.Response(status=503, text="dashboard assets unavailable")
    try:
        payload = verify_token(token, bot_token=bot_token)
    except InvalidTokenError as exc:
        logger.debug("rejected miniapp token: %s", exc)
        return web.Response(status=403, text="invalid or expired token")

    meta = (
        f'<meta name="ccgram-payload" '
        f'data-window-id="{html.escape(str(payload.window_id), quote=True)}" '
        f'data-user-id="{html.escape(str(payload.user_id), quote=True)}" '
        f'data-exp="{html.escape(str(payload.exp), quote=True)}">'
    )
    body = _render_index(meta)
    return web.Response(text=body, content_type="text/html")


def build_app(
    *,
    bot_token: str,
    terminal_capture: PaneCapture | None = None,
    pane_capture: PaneByIdCapture | None = None,
    pane_list: PaneList | None = None,
    transcript_reader: TranscriptReader | None = None,
) -> web.Application:
    app = web.Application()
    app[_BOT_TOKEN_KEY] = bot_token
    app.router.add_get("/healthz", _handle_health)
    app.router.add_get("/app/{token}", _handle_app)
    app.router.add_static("/static/", path=_STATIC_DIR, show_index=False)
    register_terminal_routes(
        app,
        bot_token=bot_token,
        capture=terminal_capture,
        pane_capture=pane_capture,
        pane_list=pane_list,
    )
    register_transcript_routes(app, bot_token=bot_token, reader=transcript_reader)
    return app


async def start_server(
    *,
    bot_token: str,
    host: str,
    port: int,
    app_factory: Callable[..., web.Application] | None = None,
) -> web.AppRunner:
    factory = app_factory or build_app
    app = factory(bot_token=bot_token)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("miniapp server listening on %s:%d", host, port)
    return runner


async def stop_server(runner: web.AppRunner) -> None:
    await runner.cleanup()
    logger.info("miniapp server stopped")
