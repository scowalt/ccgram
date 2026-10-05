from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import structlog

if TYPE_CHECKING:
    from ccgram.multiplexer.base import Multiplexer

logger = structlog.get_logger()


class UnknownMultiplexerError(LookupError):
    pass


def _make_tmux() -> Multiplexer:
    from ccgram.multiplexer.tmux import tmux_manager

    return tmux_manager


def _make_herdr() -> Multiplexer:
    from ccgram.multiplexer.herdr import HerdrManager

    return HerdrManager()


_FACTORIES: dict[str, Callable[[], Multiplexer]] = {
    "tmux": _make_tmux,
    "herdr": _make_herdr,
}

_instances: dict[str, Multiplexer] = {}


def multiplexer_names() -> list[str]:
    return list(_FACTORIES)


def get_multiplexer(name: str) -> Multiplexer:
    cached = _instances.get(name)
    if cached is not None:
        return cached
    factory = _FACTORIES.get(name)
    if factory is None:
        available = ", ".join(sorted(_FACTORIES)) or "(none)"
        raise UnknownMultiplexerError(
            f"Unknown multiplexer {name!r}. Available: {available}"
        )
    instance = factory()
    _instances[name] = instance
    logger.debug("Resolved multiplexer backend %r", name)
    return instance


def _reset_multiplexer_cache_for_testing() -> None:
    _instances.clear()
