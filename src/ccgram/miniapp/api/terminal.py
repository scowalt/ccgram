from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from ...utils import log_throttled
from ..auth import (
    InvalidTokenError,
    TokenPayload,
    authorize_api_request,
    init_data_user_id,
    validate_init_data,
    verify_token,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 0.2

MAX_FRAME_BYTES = 64 * 1024

_BOT_TOKEN_KEY = web.AppKey("bot_token", str)
_CAPTURE_KEY: web.AppKey[Callable[[str], Awaitable[str | None]]] = web.AppKey(
    "terminal_capture"
)
_PANE_CAPTURE_KEY: web.AppKey[Callable[[str, str], Awaitable[str | None]]] = web.AppKey(
    "terminal_pane_capture"
)
_PANE_LIST_KEY: web.AppKey[Callable[[str], Awaitable[list[dict[str, Any]]]]] = (
    web.AppKey("terminal_pane_list")
)
_POLL_INTERVAL_KEY = web.AppKey("terminal_poll_interval", float)


async def _default_capture(window_id: str) -> str | None:
    from ...multiplexer import multiplexer as tmux_manager

    return await tmux_manager.capture_pane(window_id, with_ansi=True)


async def _default_pane_capture(window_id: str, pane_id: str) -> str | None:
    from ...multiplexer import multiplexer as tmux_manager

    return await tmux_manager.capture_pane_by_id(
        pane_id, with_ansi=True, window_id=window_id
    )


async def _default_pane_list(window_id: str) -> list[dict[str, Any]]:
    from ...multiplexer import multiplexer as tmux_manager

    from ...window_state_ports.pane_state import list_pane_projections

    panes = await tmux_manager.list_panes(window_id)
    persisted = {p.pane_id: p for p in list_pane_projections(window_id)}
    out: list[dict[str, Any]] = []
    for pane in panes:
        info = persisted.get(pane.pane_id)
        out.append(
            {
                "pane_id": pane.pane_id,
                "index": pane.index,
                "active": pane.active,
                "command": pane.command,
                "width": pane.width,
                "height": pane.height,
                "name": info.name if info else None,
                "state": info.state if info else ("active" if pane.active else "idle"),
                "subscribed": bool(info.subscribed) if info else False,
            }
        )
    return out


def _hash_frame(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_FRAME_BYTES:
        return text
    return encoded[:MAX_FRAME_BYTES].decode("utf-8", errors="ignore")


_INIT_DATA_HEADER = "X-Telegram-Init-Data"

_WS_AUTH_TIMEOUT = 5.0

_WS_AUTH_TIMEOUT_CODE = 4001
_WS_AUTH_BAD_FRAME_CODE = 4002
_WS_AUTH_FAILED_CODE = 4003


def _init_data_from_header(request: web.Request) -> str | None:
    raw = request.headers.get(_INIT_DATA_HEADER)
    return raw or None


async def _send_json(ws: web.WebSocketResponse, payload: dict[str, Any]) -> bool:
    if ws.closed:
        return False
    try:
        await ws.send_json(payload)
    except ConnectionResetError, RuntimeError:
        return False
    return True


async def _authenticate_websocket(
    ws: web.WebSocketResponse,
    *,
    bot_token: str,
    payload: TokenPayload,
) -> bool:
    try:
        first = await asyncio.wait_for(ws.receive(), timeout=_WS_AUTH_TIMEOUT)
    except TimeoutError:
        await ws.close(code=_WS_AUTH_TIMEOUT_CODE, message=b"auth timeout")
        return False
    if first.type != WSMsgType.TEXT:
        await ws.close(code=_WS_AUTH_BAD_FRAME_CODE, message=b"expected auth frame")
        return False
    try:
        msg = json.loads(first.data)
    except json.JSONDecodeError:
        await ws.close(code=_WS_AUTH_BAD_FRAME_CODE, message=b"auth not json")
        return False
    init_data = msg.get("init_data") if isinstance(msg, dict) else None
    if not isinstance(init_data, str) or not init_data:
        await ws.close(code=_WS_AUTH_FAILED_CODE, message=b"missing initData")
        return False
    try:
        params = validate_init_data(init_data, bot_token=bot_token)
        if init_data_user_id(params) != payload.user_id:
            raise InvalidTokenError("user mismatch")
    except InvalidTokenError as exc:
        logger.debug("rejected ws auth: %s", exc)
        await ws.close(code=_WS_AUTH_FAILED_CODE, message=b"auth failed")
        return False
    return True


async def _terminal_handler(request: web.Request) -> web.StreamResponse:
    token = request.match_info["token"]
    bot_token = request.app[_BOT_TOKEN_KEY]
    capture = request.app[_CAPTURE_KEY]
    pane_capture = request.app[_PANE_CAPTURE_KEY]
    pane_list = request.app[_PANE_LIST_KEY]
    interval = request.app[_POLL_INTERVAL_KEY]

    try:
        payload = verify_token(token, bot_token=bot_token)
    except InvalidTokenError as exc:
        logger.debug("rejected terminal websocket token: %s", exc)
        return web.Response(status=403, text="invalid or expired token")

    pane_id = (request.query.get("pane") or "").strip() or None

    if pane_id is not None:
        try:
            panes = await pane_list(payload.window_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "pane membership check failed for %s pane=%s",
                payload.window_id,
                pane_id,
            )
            return web.Response(status=503, text="pane lookup failed")
        if pane_id not in {p.get("pane_id") for p in panes}:
            return web.Response(status=403, text="pane not in window")

    ws = web.WebSocketResponse(heartbeat=30.0)
    await ws.prepare(request)

    if not await _authenticate_websocket(ws, bot_token=bot_token, payload=payload):
        return ws

    await _send_json(
        ws,
        {
            "type": "hello",
            "window_id": payload.window_id,
            "interval": interval,
            "pane_id": pane_id,
        },
    )

    streamer = asyncio.create_task(
        _stream_loop(ws, payload.window_id, capture, pane_capture, pane_id, interval)
    )
    try:
        async for msg in ws:
            if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        streamer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await streamer
        if not ws.closed:
            await ws.close()
    return ws


async def _panes_handler(request: web.Request) -> web.Response:
    token = request.match_info["token"]
    bot_token = request.app[_BOT_TOKEN_KEY]
    pane_list = request.app[_PANE_LIST_KEY]

    init_data = _init_data_from_header(request)
    try:
        payload = authorize_api_request(
            bot_token=bot_token, token=token, init_data=init_data
        )
    except InvalidTokenError as exc:
        logger.debug("rejected panes list token: %s", exc)
        return web.Response(status=403, text="invalid or expired token")

    try:
        panes = await pane_list(payload.window_id)
    except Exception:  # noqa: BLE001
        logger.exception("pane list failed for %s", payload.window_id)
        return web.json_response({"error": "list failed"}, status=500)

    return web.json_response({"window_id": payload.window_id, "panes": list(panes)})


_CAPTURE_TIMEOUT = 5.0


async def _stream_loop(
    ws: web.WebSocketResponse,
    window_id: str,
    capture: Callable[[str], Awaitable[str | None]],
    pane_capture: Callable[[str, str], Awaitable[str | None]],
    pane_id: str | None,
    interval: float,
) -> None:
    last_hash: str | None = None
    while not ws.closed:
        try:
            if pane_id is None:
                text = await asyncio.wait_for(
                    capture(window_id), timeout=_CAPTURE_TIMEOUT
                )
            else:
                text = await asyncio.wait_for(
                    pane_capture(window_id, pane_id), timeout=_CAPTURE_TIMEOUT
                )
        except TimeoutError:
            log_throttled(
                logger,
                f"term-capture-timeout:{window_id}:{pane_id}",
                "terminal capture timeout for %s pane=%s",
                window_id,
                pane_id,
            )
            await _send_json(ws, {"type": "error", "message": "capture timeout"})
            await asyncio.sleep(interval)
            continue
        except Exception:  # noqa: BLE001
            log_throttled(
                logger,
                f"term-capture-failed:{window_id}:{pane_id}",
                "terminal capture failed for %s pane=%s",
                window_id,
                pane_id,
            )
            await _send_json(ws, {"type": "error", "message": "capture failed"})
            await asyncio.sleep(interval)
            continue

        if text is None:
            text = ""
        truncated = _truncate(text)
        digest = _hash_frame(truncated)
        if digest != last_hash:
            sent = await _send_json(
                ws,
                {"type": "frame", "text": truncated, "hash": digest},
            )
            if not sent:
                return
            last_hash = digest

        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return


def register_terminal_routes(
    app: web.Application,
    *,
    bot_token: str,
    capture: Callable[[str], Awaitable[str | None]] | None = None,
    pane_capture: Callable[[str, str], Awaitable[str | None]] | None = None,
    pane_list: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> None:
    interval = max(0.05, float(poll_interval))
    app[_BOT_TOKEN_KEY] = bot_token
    app[_CAPTURE_KEY] = capture or _default_capture
    app[_PANE_CAPTURE_KEY] = pane_capture or _default_pane_capture
    app[_PANE_LIST_KEY] = pane_list or _default_pane_list
    app[_POLL_INTERVAL_KEY] = interval
    app.router.add_get("/ws/terminal/{token}", _terminal_handler)
    app.router.add_get("/api/panes/{token}", _panes_handler)


__all__ = [
    "DEFAULT_POLL_INTERVAL",
    "MAX_FRAME_BYTES",
    "register_terminal_routes",
]
