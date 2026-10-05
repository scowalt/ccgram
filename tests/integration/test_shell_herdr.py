from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

import pytest

from ccgram.handlers.shell.shell_capture import (
    _CommandOutput,
    _capture_with_scrollback,
    _extract_command_output,
)
from ccgram.multiplexer import (
    _reset_multiplexer_for_testing,
    install_multiplexer,
    multiplexer,
)
from ccgram.multiplexer.herdr import HerdrError, HerdrManager
from ccgram.providers.process_detection import (
    classify_provider_from_argv,
    detect_provider_cached,
)
from ccgram.providers.shell_infra import (
    KNOWN_SHELLS,
    _is_interactive_shell,
    has_prompt_marker,
    setup_shell_prompt,
)

pytestmark = [pytest.mark.integration, pytest.mark.herdr]

_SCROLLBACK = 500
_POLL_TIMEOUT = 12.0


def _socket_or_skip() -> str:
    socket = os.environ.get("HERDR_SOCKET_PATH", "")
    if not socket or not os.path.exists(socket):
        pytest.skip("herdr socket not available ($HERDR_SOCKET_PATH unset/missing)")
    return socket


@pytest.fixture
async def herdr_shell(tmp_path):
    socket = _socket_or_skip()
    mgr = HerdrManager(socket_path=socket)
    try:
        await mgr.ensure_session()
    except HerdrError as exc:
        pytest.skip(f"herdr server unavailable: {exc}")

    install_multiplexer(mgr)
    ok, _, _, window_id = await mgr.create_window(
        str(tmp_path), window_name="ccgram-shell-itest", start_agent=False
    )
    if not ok or not window_id:
        _reset_multiplexer_for_testing()
        pytest.skip("herdr could not create a shell pane")

    try:
        yield mgr, window_id
    finally:
        await mgr.kill_window(window_id)
        _reset_multiplexer_for_testing()


async def _poll(
    window_id: str, predicate: Callable[[str], _CommandOutput | None]
) -> _CommandOutput:
    deadline = asyncio.get_event_loop().time() + _POLL_TIMEOUT
    last = ""
    while asyncio.get_event_loop().time() < deadline:
        cap = await _capture_with_scrollback(window_id, history=_SCROLLBACK)
        last = cap.text if cap else ""
        hit = predicate(last)
        if hit is not None:
            return hit
        await asyncio.sleep(0.3)
    raise AssertionError(f"predicate never satisfied; last capture:\n{last}")


async def _marker_within(window_id: str, timeout: float) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await has_prompt_marker(window_id):
            return True
        await asyncio.sleep(0.3)
    return False


async def _setup_shell_marker(window_id: str) -> None:
    for _ in range(5):
        await setup_shell_prompt(window_id)
        if await _marker_within(window_id, timeout=2.5):
            return
    cap = await multiplexer.capture_pane(window_id)
    raise AssertionError(
        f"prompt marker never appeared:\n{cap if cap else '<no capture>'}"
    )


async def test_shell_classified_from_herdr_process_info_not_tty(herdr_shell) -> None:
    mgr, window_id = herdr_shell

    fg = await multiplexer.foreground(window_id)
    assert fg is not None
    assert fg.tty == ""
    assert mgr.capabilities.exposes_pane_tty is False
    assert fg.pid > 0
    assert fg.argv

    basename = fg.argv[0].rsplit("/", 1)[-1].lstrip("-")
    assert basename in KNOWN_SHELLS, fg.argv

    assert classify_provider_from_argv(fg.argv) == "shell"
    assert await detect_provider_cached(window_id, fg) == "shell"

    assert await _is_interactive_shell(window_id) is True


async def test_shell_flow_marker_run_isolation_exit_interrupt_clear(
    herdr_shell,
) -> None:
    _, window_id = herdr_shell

    await _setup_shell_marker(window_id)

    marker = "herdr_shell_out_42"
    assert await multiplexer.send(window_id, f"echo {marker}") is True

    def _has_output(text: str) -> _CommandOutput | None:
        out = _extract_command_output(text)
        return out if marker in out.text else None

    ok_out = await _poll(window_id, _has_output)
    assert marker in ok_out.text
    assert ok_out.exit_code == 0

    assert await multiplexer.send(window_id, 'sh -c "exit 3"') is True

    def _has_exit_3(text: str) -> _CommandOutput | None:
        out = _extract_command_output(text)
        return out if out.exit_code == 3 else None

    fail_out = await _poll(window_id, _has_exit_3)
    assert fail_out.exit_code == 3

    assert (
        await multiplexer.send_keys(window_id, "C-c", enter=False, literal=False)
        is True
    )
    assert await multiplexer.send_keys(window_id, "clear", raw=True) is True


async def test_shell_scrollback_clamp_surfaces_truncation(herdr_shell) -> None:
    _, window_id = herdr_shell

    sentinel = "clamp_sentinel_1500"
    assert await multiplexer.send(window_id, f"seq 1 1500; echo {sentinel}") is True

    async def _scrollback_has(needle: str) -> None:
        deadline = asyncio.get_event_loop().time() + _POLL_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            cap = await _capture_with_scrollback(window_id, history=_SCROLLBACK)
            if cap and needle in cap.text:
                return
            await asyncio.sleep(0.3)
        raise AssertionError(f"{needle!r} never appeared in scrollback")

    await _scrollback_has(sentinel)

    cap = await _capture_with_scrollback(window_id, history=5000)
    assert cap is not None
    assert cap.truncated is True
    assert sentinel in cap.text
