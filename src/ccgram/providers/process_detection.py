from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

import structlog

from ..topic_state_registry import topic_state
from .shell import KNOWN_SHELLS as _KNOWN_SHELLS

if TYPE_CHECKING:
    from ..multiplexer.base import ForegroundInfo

logger = structlog.get_logger()

_WRAPPER_TOKENS = frozenset(
    {"sudo", "env", "node", "bun", "npx", "bunx", "uv", "python", "python3"}
)

_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

_PROVIDER_BASENAMES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"claude", "ce", "cc-mirror", "zai"}), "claude"),
    (frozenset({"codex"}), "codex"),
    (frozenset({"gemini"}), "gemini"),
    (frozenset({"pi"}), "pi"),
)

_PROVIDER_PATH_MARKERS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("claude-code", "cc-team"), "claude"),
    (("@openai/codex", "/codex/", "/codex-"), "codex"),
    (("gemini-cli",), "gemini"),
    (("@mariozechner/pi-coding-agent", "/pi-coding-agent/"), "pi"),
)


JS_RUNTIMES = frozenset({"node", "bun", "npx", "bunx"})


def _match_token(token: str) -> str:
    basename = os.path.basename(token).lower().lstrip("-")

    for names, provider in _PROVIDER_BASENAMES:
        if basename in names or basename.startswith(f"{provider}-"):
            return provider
    if basename in _KNOWN_SHELLS:
        return "shell"

    token_lower = token.lower()
    for markers, provider in _PROVIDER_PATH_MARKERS:
        if any(m in token_lower for m in markers):
            return provider

    return ""


def classify_provider_from_argv(argv: Sequence[str]) -> str:
    for token in argv:
        cleaned = os.path.basename(token).lower().lstrip("-")
        if cleaned in _WRAPPER_TOKENS:
            continue
        provider = _match_token(token)
        if provider:
            return provider
        if token.startswith("-") or _ENV_ASSIGNMENT_RE.match(token):
            continue
        return ""

    return ""


def classify_provider_from_args(args: str) -> str:
    return classify_provider_from_argv(args.split())


_pgid_cache: dict[str, tuple[int, str]] = {}


def get_cached_foreground_pgid(window_id: str) -> int:
    cached = _pgid_cache.get(window_id)
    return cached[0] if cached else 0


async def detect_provider_cached(window_id: str, fg: ForegroundInfo | None) -> str:
    if fg is None or not fg.argv or fg.pgid == 0:
        return ""

    cached = _pgid_cache.get(window_id)
    if cached and cached[0] == fg.pgid:
        return cached[1]

    provider = classify_provider_from_argv(fg.argv)
    if provider:
        _pgid_cache[window_id] = (fg.pgid, provider)
    return provider


@topic_state.register("window")
def clear_detection_cache(window_id: str | None = None) -> None:
    if window_id is None:
        _pgid_cache.clear()
    else:
        _pgid_cache.pop(window_id, None)
