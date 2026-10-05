from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from .base import WindowRef


class _ReconciliationWindowLister(Protocol):
    async def list_windows_for_reconciliation(self) -> list[WindowRef] | None:
        pass


async def list_windows_for_reconciliation(
    backend: object | None = None,
) -> list[WindowRef] | None:
    if backend is None:
        from . import get_active_multiplexer

        backend = get_active_multiplexer()

    method = getattr(backend, "list_windows_for_reconciliation", None)
    if not callable(method):
        if getattr(backend, "last_window_scan_failed", False):
            return None
        legacy_list = getattr(backend, "list_windows", None)
        if callable(legacy_list):
            legacy_lister = cast(
                "Callable[[], Awaitable[list[WindowRef]]]", legacy_list
            )
            return await legacy_lister()
        name = getattr(getattr(backend, "capabilities", None), "name", "unknown")
        raise RuntimeError(
            f"Multiplexer backend {name!r} does not support reconciliation listings"
        )
    lister = cast("_ReconciliationWindowLister", backend)
    return await lister.list_windows_for_reconciliation()
