from __future__ import annotations

import asyncio
import re

from ..topic_state_registry import topic_state

_vim_state: dict[str, bool] = {}

_vim_locks: dict[str, asyncio.Lock] = {}

_VIM_INSERT_RE = re.compile(r"^--\s*INSERT\s*--\s*$")


def has_insert_indicator(pane_text: str) -> bool:
    return any(
        _VIM_INSERT_RE.search(line.strip()) for line in pane_text.splitlines()[-3:]
    )


def notify_vim_insert_seen(window_id: str) -> None:
    _vim_state[window_id] = True


@topic_state.register("window")
def clear_vim_state(window_id: str) -> None:
    _vim_state.pop(window_id, None)
    _vim_locks.pop(window_id, None)


def reset_vim_state() -> None:
    _vim_state.clear()
    _vim_locks.clear()
