from __future__ import annotations

EXPANDABLE_QUOTE_START = "\x02EXPQUOTE_START\x02"
EXPANDABLE_QUOTE_END = "\x02EXPQUOTE_END\x02"

_EXPANDABLE_QUOTE_MAX_CHARS = 3500


def format_expandable_quote(text: str) -> str:
    if len(text) > _EXPANDABLE_QUOTE_MAX_CHARS:
        text = (
            text[:_EXPANDABLE_QUOTE_MAX_CHARS]
            + f"\n\n\u2026 (truncated, {len(text)} chars total)"
        )
    return f"{EXPANDABLE_QUOTE_START}{text}{EXPANDABLE_QUOTE_END}"
