from ccgram.last_unit import extract_last_shell_block


_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_BRACKETED_PASTE = "\x1b[?2004h"
_CURSOR_UP = "\x1b[A"

BARE = f"user@host $ {_DIM}⌘0⌘{_RESET}"
ECHO = f"user@host $ {_DIM}⌘0⌘{_RESET} ls -la"
ECHO_FAILED = f"user@host $ {_DIM}⌘1⌘{_RESET} ls /missing"
BARE_AFTER_FAIL = f"user@host $ {_DIM}⌘1⌘{_RESET}"
OUTPUT = "total 8\ndrwxr-xr-x  2 user group 64 Jan  1 00:00 ."


def _scrollback(*lines: str) -> str:
    return "\n".join(lines)


def test_extract_happy_path(_wrap_mode: None) -> None:
    scrollback = _scrollback(
        "some earlier output",
        ECHO,
        OUTPUT,
        BARE,
    )
    result = extract_last_shell_block(scrollback)
    assert result is not None
    assert result == _scrollback(ECHO, OUTPUT, BARE)


def test_extract_no_markers_returns_none() -> None:
    scrollback = _scrollback("line one", "line two", "line three")
    assert extract_last_shell_block(scrollback) is None


def test_extract_only_bare_prompt_no_echo_returns_none(_wrap_mode: None) -> None:
    scrollback = _scrollback("some output", "more output", BARE)
    assert extract_last_shell_block(scrollback) is None


def test_extract_command_running_returns_none(_wrap_mode: None) -> None:
    scrollback = _scrollback("earlier output", ECHO, "partial output")
    assert extract_last_shell_block(scrollback) is None


def test_extract_with_bracketed_paste_around_marker(_wrap_mode: None) -> None:
    bare_with_paste = f"{_BRACKETED_PASTE}{BARE}{_BRACKETED_PASTE}"
    echo_with_paste = f"{_BRACKETED_PASTE}{ECHO}"
    scrollback = _scrollback("earlier", echo_with_paste, OUTPUT, bare_with_paste)
    result = extract_last_shell_block(scrollback)
    assert result == _scrollback(echo_with_paste, OUTPUT, bare_with_paste)


def test_extract_bare_prompt_with_trailing_cursor_movement(_wrap_mode: None) -> None:
    bare_with_cursor = f"{BARE}{_CURSOR_UP}"
    scrollback = _scrollback("earlier", ECHO, OUTPUT, bare_with_cursor)
    result = extract_last_shell_block(scrollback)
    assert result == _scrollback(ECHO, OUTPUT, bare_with_cursor)


def test_extract_picks_last_completed_command(_wrap_mode: None) -> None:
    scrollback = _scrollback(
        f"user@host $ {_DIM}⌘0⌘{_RESET} echo old",
        "old",
        ECHO_FAILED,
        "ls: /missing: No such file or directory",
        BARE_AFTER_FAIL,
    )
    result = extract_last_shell_block(scrollback)
    assert result == _scrollback(
        ECHO_FAILED, "ls: /missing: No such file or directory", BARE_AFTER_FAIL
    )


def test_extract_replace_mode_happy_path() -> None:
    echo = "ccgram:0❯ ls -la"
    bare = "ccgram:0❯ "
    scrollback = _scrollback("earlier", echo, OUTPUT, bare)
    result = extract_last_shell_block(scrollback)
    assert result == _scrollback(echo, OUTPUT, bare)
