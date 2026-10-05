import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from .utils import atomic_write_json

logger = structlog.get_logger()

_SaveError = (OSError, TypeError, ValueError)


class StatePersistence:
    def __init__(self, path: Path, serialize_fn: Callable[[], dict[str, Any]]) -> None:
        self._path = path
        self._serialize_fn = serialize_fn
        self._save_timer: asyncio.TimerHandle | None = None
        self._dirty = False

    def schedule_save(self) -> None:
        self._dirty = True
        if self._save_timer is not None:
            self._save_timer.cancel()
        try:
            loop = asyncio.get_running_loop()
            self._save_timer = loop.call_later(0.5, self._do_save)
        except RuntimeError:
            self._do_save()

    def _do_save(self) -> None:
        self._save_timer = None
        try:
            state = self._serialize_fn()
            atomic_write_json(self._path, state)
            self._dirty = False
        except _SaveError:
            logger.exception("Failed to save state")

    def flush(self) -> None:
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        if self._dirty:
            self._do_save()

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, ValueError, OSError) as e:
            logger.warning("Failed to load state: %s", e)
            return {}
