from __future__ import annotations

import asyncio
import functools
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

_DEFAULT_MARKER = "ccgram"


_VALID_PROMPT_MODES = frozenset({"wrap", "replace"})
_WARNED_INVALID_MODE = False


def _get_prompt_mode() -> str:
    global _WARNED_INVALID_MODE  # noqa: PLW0603
    from ccgram.config import config

    mode = getattr(config, "prompt_mode", "wrap") or "wrap"
    if mode not in _VALID_PROMPT_MODES:
        if not _WARNED_INVALID_MODE:
            _WARNED_INVALID_MODE = True
            structlog.get_logger().warning(
                "Invalid CCGRAM_PROMPT_MODE=%r, defaulting to 'wrap'", mode
            )
        return "wrap"
    return mode


def _get_marker_prefix() -> str:
    from ccgram.config import config

    return getattr(config, "prompt_marker", _DEFAULT_MARKER) or _DEFAULT_MARKER


@functools.cache
def _compile_replace_re(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefix)}:(\d+)❯\s?(.*)")


_WRAP_RE = re.compile(r"⌘(\d+)⌘\s?(.*)$")


@dataclass(frozen=True)
class PromptMatch:
    sequence_number: int
    """Monotonic counter (exit code of the last command)."""

    trailing_text: str
    """Command text after the marker (empty string when the shell is idle)."""

    exit_code: int
    """Exit code of the last command (same value as *sequence_number*)."""

    raw_line: str
    """Original terminal line that matched."""


def _match_to_prompt_match(m: re.Match[str], line: str) -> PromptMatch:
    num = int(m.group(1))
    return PromptMatch(
        sequence_number=num,
        trailing_text=m.group(2),
        exit_code=num,
        raw_line=line,
    )


def match_prompt(line: str) -> PromptMatch | None:
    if _get_prompt_mode() == "replace":
        m = _compile_replace_re(_get_marker_prefix()).match(line)
    else:
        m = _WRAP_RE.search(line)
    if m is None:
        return None
    return _match_to_prompt_match(m, line)


KNOWN_SHELLS = frozenset({"bash", "zsh", "fish", "sh", "dash", "tcsh", "csh", "ksh"})


async def has_prompt_marker(
    window_id: str,
    *,
    capture_fn: Callable[[str], Awaitable[str | None]] | None = None,
) -> bool:
    if capture_fn is None:
        from ccgram.multiplexer import multiplexer as tmux_manager

        capture_fn = tmux_manager.capture_pane
    capture = await capture_fn(window_id)
    if not capture:
        return False
    return any(match_prompt(line) for line in capture.rstrip().splitlines()[-5:])


def get_shell_name() -> str:
    return os.environ.get("SHELL", "").rsplit("/", 1)[-1]


async def detect_pane_shell(window_id: str) -> str:
    from ccgram.multiplexer import multiplexer as tmux_manager

    window = await tmux_manager.find_window_by_id(window_id)
    if window and window.pane_current_command:
        tokens = window.pane_current_command.split()
        if tokens:
            cleaned = os.path.basename(tokens[0]).lstrip("-")
            if cleaned in KNOWN_SHELLS:
                return cleaned

    fg = await tmux_manager.foreground(window_id)
    if fg and fg.argv:
        cleaned = os.path.basename(fg.argv[0]).lstrip("-")
        if cleaned in KNOWN_SHELLS:
            return cleaned

    return get_shell_name()


def _wrap_setup_commands(shell: str) -> str:
    fish = (
        "builtin functions --query __ccgram_orig_prompt; or begin; "
        "builtin functions --copy fish_prompt __ccgram_orig_prompt 2>/dev/null; "
        "or function __ccgram_orig_prompt; end; "
        "function fish_prompt; "
        "set -l __s $status; "
        "__ccgram_orig_prompt; "
        "set_color brblack; printf '⌘%d⌘ ' $__s; set_color normal; "
        "end; clear; end"
    )
    bash = (
        "type __ccgram_sc >/dev/null 2>&1 || { "
        "__ccgram_sc(){ __ccgram_x=$?; return $__ccgram_x; }; "
        'PROMPT_COMMAND="__ccgram_sc${PROMPT_COMMAND:+;$PROMPT_COMMAND}"; '
        'PS1="${PS1}\\[\\033[2m\\]⌘\\${__ccgram_x}⌘\\[\\033[0m\\] "; '
        "clear; }"
    )
    zsh = (
        '[[ "$PROMPT" == *⌘%\\?⌘* ]] || { '
        "PROMPT+=$'%{\\e[2m%}⌘%?⌘%{\\e[0m%} '; "
        "clear; }"
    )
    tcsh = 'set prompt = "${prompt}⌘$status⌘ "'
    sh = 'case "$PS1" in *⌘*⌘*) ;; *) PS1="\\$ ⌘0⌘ "; clear;; esac'
    return {
        "fish": fish,
        "bash": bash,
        "zsh": zsh,
        "tcsh": tcsh,
        "csh": tcsh,
        "sh": sh,
        "dash": sh,
        "ksh": sh,
    }.get(shell, sh)


def _replace_setup_commands(shell: str, prefix: str) -> str:
    cmds = {
        "fish": f'function fish_prompt; printf "{prefix}:$status❯ "; end',
        "bash": f"PS1='{prefix}:$?❯ '",
        "zsh": f"PROMPT='{prefix}:%?❯ '",
        "tcsh": f'set prompt = "{prefix}:$status❯ "',
        "csh": f'set prompt = "{prefix}:$status❯ "',
    }
    return cmds.get(shell, cmds["bash"])


async def _is_interactive_shell(window_id: str) -> bool:
    from ccgram.multiplexer import multiplexer

    fg = await multiplexer.foreground(window_id)
    if fg is None or not fg.argv:
        return False

    first_token = fg.argv[0]
    basename = first_token.rsplit("/", 1)[-1].lstrip("-")
    if basename not in KNOWN_SHELLS:
        return False

    return len(fg.argv) == 1


async def setup_shell_prompt(
    window_id: str,
    *,
    clear: bool = True,
    capture_fn: Callable[[str], Awaitable[str | None]] | None = None,
    send_keys_fn: Callable[..., Awaitable[bool]] | None = None,
) -> None:
    from ccgram.config import config

    if config.own_window_id and window_id == config.own_window_id:
        return

    if not await _is_interactive_shell(window_id):
        return

    if await has_prompt_marker(window_id, capture_fn=capture_fn):
        return

    if send_keys_fn is None:
        from ccgram.multiplexer import multiplexer as tmux_manager

        send_keys_fn = tmux_manager.send_keys

    await send_keys_fn(window_id, "C-c", enter=False, literal=False)
    await asyncio.sleep(0.1)

    shell = await detect_pane_shell(window_id)
    mode = _get_prompt_mode()
    if mode == "replace":
        cmd = _replace_setup_commands(shell, _get_marker_prefix())
    else:
        cmd = _wrap_setup_commands(shell)
    await send_keys_fn(window_id, cmd, raw=True)
    await asyncio.sleep(0.3)
    if clear:
        await send_keys_fn(window_id, "clear", raw=True)
