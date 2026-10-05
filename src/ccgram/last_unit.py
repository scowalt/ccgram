from __future__ import annotations

import re

from .providers.shell_infra import match_prompt

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]")


def _strip_ansi(line: str) -> str:
    return _ANSI_RE.sub("", line)


def extract_last_shell_block(scrollback_text: str) -> str | None:
    lines = scrollback_text.splitlines()
    if not lines:
        return None

    bare_idx: int | None = None
    for i in range(len(lines) - 1, -1, -1):
        m = match_prompt(_strip_ansi(lines[i]))
        if m is not None and not _strip_ansi(m.trailing_text).strip():
            bare_idx = i
            break

    if bare_idx is None:
        return None

    echo_idx: int | None = None
    for i in range(bare_idx - 1, -1, -1):
        m = match_prompt(_strip_ansi(lines[i]))
        if m is not None and _strip_ansi(m.trailing_text).strip():
            echo_idx = i
            break

    if echo_idx is None:
        return None

    return "\n".join(lines[echo_idx:])
