from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


EVENTS_SCHEMA_VERSION: int = 1
SESSION_MAP_SCHEMA_VERSION: int = 1


class StateFileValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EventLogRecord:
    schema_version: int
    ts: float
    event: str
    window_key: str
    session_id: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SessionMapEntry:
    schema_version: int
    session_id: str
    cwd: str
    window_name: str
    transcript_path: str
    provider_name: str


_REQUIRED_EVENT_FIELDS: tuple[str, ...] = ("event", "window_key", "session_id")


def parse_event_record(raw: dict[str, Any]) -> EventLogRecord:
    if not isinstance(raw, dict):
        raise StateFileValidationError(
            f"Event record must be a JSON object, got {type(raw).__name__!r}"
        )
    version = raw.get("schema_version")
    if version is None:
        version = 1
    elif not isinstance(version, int) or version > EVENTS_SCHEMA_VERSION:
        raise StateFileValidationError(
            f"Unsupported events schema_version {version!r}; "
            f"expected <= {EVENTS_SCHEMA_VERSION}"
        )

    missing = [f for f in _REQUIRED_EVENT_FIELDS if not raw.get(f)]
    if missing:
        raise StateFileValidationError(
            f"Event record missing required fields: {missing}"
        )

    return EventLogRecord(
        schema_version=version,
        ts=raw.get("ts") or 0.0,
        event=raw["event"],
        window_key=raw["window_key"],
        session_id=raw["session_id"],
        data=raw.get("data") or {},
    )


def serialize_event_record(
    event_type: str,
    session_id: str,
    window_key: str,
    data: dict[str, Any],
    *,
    ts: float | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": EVENTS_SCHEMA_VERSION,
        "ts": ts if ts is not None else time.time(),
        "event": event_type,
        "window_key": window_key,
        "session_id": session_id,
        "data": data,
    }


_REQUIRED_SESSION_MAP_FIELDS: tuple[str, ...] = ("session_id",)


def parse_session_map_entry(raw: dict[str, Any]) -> SessionMapEntry:
    if not isinstance(raw, dict):
        raise StateFileValidationError(
            f"Session map entry must be a JSON object, got {type(raw).__name__!r}"
        )
    version = raw.get("schema_version")
    if version is None:
        version = 1
    elif not isinstance(version, int) or version > SESSION_MAP_SCHEMA_VERSION:
        raise StateFileValidationError(
            f"Unsupported session_map schema_version {version!r}; "
            f"expected <= {SESSION_MAP_SCHEMA_VERSION}"
        )

    missing = [f for f in _REQUIRED_SESSION_MAP_FIELDS if not raw.get(f)]
    if missing:
        raise StateFileValidationError(
            f"Session map entry missing required fields: {missing}"
        )

    return SessionMapEntry(
        schema_version=version,
        session_id=raw["session_id"],
        cwd=raw.get("cwd", ""),
        window_name=raw.get("window_name", ""),
        transcript_path=raw.get("transcript_path", ""),
        provider_name=raw.get("provider_name", ""),
    )


def serialize_session_map_entry(
    session_id: str,
    cwd: str,
    window_name: str,
    transcript_path: str,
    provider_name: str,
) -> dict[str, Any]:
    return {
        "schema_version": SESSION_MAP_SCHEMA_VERSION,
        "session_id": session_id,
        "cwd": cwd,
        "window_name": window_name,
        "transcript_path": transcript_path,
        "provider_name": provider_name,
    }
