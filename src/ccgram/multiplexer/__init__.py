from __future__ import annotations

from typing import Any, cast

from ccgram.multiplexer.base import Multiplexer
from ccgram.multiplexer.registry import (
    UnknownMultiplexerError,
    get_multiplexer,
    multiplexer_names,
)

__all__ = [
    "Multiplexer",
    "UnknownMultiplexerError",
    "get_active_multiplexer",
    "get_multiplexer",
    "install_multiplexer",
    "multiplexer",
    "multiplexer_names",
]


_active_multiplexer: Multiplexer | None = None


def get_active_multiplexer() -> Multiplexer:
    if _active_multiplexer is None:
        raise RuntimeError(
            "Multiplexer not yet wired. "
            "bootstrap_application() must install a backend before use."
        )
    return _active_multiplexer


def install_multiplexer(backend: Multiplexer) -> None:
    global _active_multiplexer
    _active_multiplexer = backend


def _reset_multiplexer_for_testing() -> None:
    global _active_multiplexer
    _active_multiplexer = None


class _MultiplexerProxy:
    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(get_active_multiplexer(), name)

    def __repr__(self) -> str:
        if _active_multiplexer is None:
            return "<MultiplexerProxy unwired>"
        return f"<MultiplexerProxy → {_active_multiplexer!r}>"


multiplexer: Multiplexer = cast("Multiplexer", _MultiplexerProxy())
