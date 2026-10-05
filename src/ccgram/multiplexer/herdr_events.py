from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence

import structlog

from .base import AgentStatus, MuxEvent

logger = structlog.get_logger()

_SUBSCRIBED_KEY = "__subscribed__"
SUBSCRIBED: dict = {_SUBSCRIBED_KEY: True}

_EVT_AGENT_STATUS = frozenset(
    {"pane.agent_status_changed", "pane_agent_status_changed"}
)
_EVT_TAB_CLOSED = frozenset({"tab.closed", "tab_closed"})


def is_subscribed_sentinel(obj: Mapping[str, object]) -> bool:
    return bool(obj.get(_SUBSCRIBED_KEY))


async def open_socket_stream(
    socket_path: str, subscriptions: Sequence[Mapping[str, object]]
) -> AsyncIterator[dict]:
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        request = json.dumps(
            {
                "id": "ccgram-events",
                "method": "events.subscribe",
                "params": {"subscriptions": list(subscriptions)},
            }
        )
        writer.write((request + "\n").encode())
        await writer.drain()

        ack = await reader.readline()
        if ack:
            with contextlib.suppress(ValueError):
                payload = json.loads(ack)
                if isinstance(payload, dict) and "error" in payload:
                    logger.warning("herdr events.subscribe error: %s", payload["error"])
        yield SUBSCRIBED

        while True:
            line = await reader.readline()
            if not line:
                return
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except ValueError:
                logger.debug("herdr event stream: non-JSON line")
                continue
            if isinstance(obj, dict) and "event" in obj:
                yield obj
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


def translate_event(
    obj: Mapping[str, object], pane_to_window: Mapping[str, str]
) -> MuxEvent | None:
    event = obj.get("event")
    data = obj.get("data")
    if not isinstance(data, Mapping):
        data = {}

    if event in _EVT_AGENT_STATUS:
        window_id = pane_to_window.get(_str(data.get("pane_id")))
        if not window_id:
            return None
        return MuxEvent(
            kind="agent_status",
            window_id=window_id,
            pane_id=_str(data.get("pane_id")),
            status=AgentStatus(
                state=_str(data.get("agent_status")) or "unknown",
                agent=_str(data.get("agent")),
                custom_status=_str(data.get("custom_status")),
            ),
        )
    if event in _EVT_TAB_CLOSED:
        tab_id = _str(data.get("tab_id"))
        if tab_id and tab_id in set(pane_to_window.values()):
            return MuxEvent(kind="window_died", window_id=tab_id)
        return None
    return None


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""
