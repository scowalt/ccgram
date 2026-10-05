from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from ccgram.multiplexer.herdr import HerdrError, HerdrManager
from ccgram.multiplexer.topic_mapping import TOPIC_PREFIX_SEPARATOR

pytestmark = [pytest.mark.integration, pytest.mark.herdr]


def _socket_or_skip() -> str:
    socket = os.environ.get("HERDR_SOCKET_PATH", "")
    if not socket or not os.path.exists(socket):
        pytest.skip("herdr socket not available ($HERDR_SOCKET_PATH unset/missing)")
    return socket


@pytest.fixture
async def herdr() -> HerdrManager:
    socket = _socket_or_skip()
    mgr = HerdrManager(socket_path=socket)
    try:
        await mgr.ensure_session()
    except HerdrError as exc:
        pytest.skip(f"herdr server unavailable: {exc}")
    return mgr


async def _capture_until(
    mgr: HerdrManager, window_id: str, needle: str, *, timeout: float = 8.0
) -> str:
    deadline = asyncio.get_event_loop().time() + timeout
    last = ""
    while asyncio.get_event_loop().time() < deadline:
        result = await mgr.capture_pane(window_id)
        last = result or ""
        if needle in last:
            return last
        await asyncio.sleep(0.3)
    raise AssertionError(f"never saw {needle!r}; last capture:\n{last}")


async def test_create_send_capture_kill_roundtrip(
    herdr: HerdrManager, tmp_path
) -> None:
    ok, _msg, _name, window_id = await herdr.create_window(
        str(tmp_path), window_name="ccgram-itest", start_agent=False
    )
    assert ok is True
    assert window_id

    try:
        marker = "ccgram_herdr_marker_42"
        assert await herdr.send(window_id, f"echo {marker}") is True
        text = await _capture_until(herdr, window_id, marker)
        assert marker in text

        panes = await herdr.list_panes(window_id)
        assert len(panes) >= 1
        assert all(p.pane_id != window_id for p in panes)

        dims = await herdr.pane_dims(window_id)
        assert dims is not None and dims.width > 0 and dims.height > 0

        fg = await herdr.foreground(window_id)
        assert fg is not None
        assert fg.pid > 0
        assert fg.argv
        assert fg.tty == ""
    finally:
        assert await herdr.kill_window(window_id) is True

    await asyncio.sleep(0.3)
    assert await herdr.find_window_by_id(window_id) is None


def _source_workspace_id(repo: str) -> str:
    out = subprocess.run(
        ["herdr", "worktree", "list", "--cwd", repo, "--json"],
        capture_output=True,
        text=True,
    ).stdout
    try:
        return json.loads(out)["result"]["source"]["source_workspace_id"] or ""
    except ValueError, KeyError, TypeError:
        return ""


async def test_create_worktree_window_delegates(herdr: HerdrManager, tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for argv in (
        ["init"],
        ["config", "user.email", "t@e"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True)
    (repo / "f.txt").write_text("hi")
    subprocess.run(
        ["git", "-C", str(repo), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )

    wt_path = str(tmp_path / "wt")
    ok, _msg, _name, window_id = await herdr.create_worktree_window(
        str(repo), wt_path, "ccg-itest", window_name="ccgram-wt-itest"
    )
    try:
        assert ok is True
        assert ":t" in window_id
        assert await herdr.find_window_by_id(window_id) is not None
        assert (Path(wt_path) / "f.txt").exists()
        branch = subprocess.run(
            ["git", "-C", wt_path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert branch == "ccg-itest"
    finally:
        source_ws = _source_workspace_id(str(repo))
        if source_ws:
            subprocess.run(
                ["herdr", "workspace", "close", source_ws], capture_output=True
            )


async def test_agent_status_returns_valid_state(herdr: HerdrManager, tmp_path) -> None:
    ok, _msg, _name, window_id = await herdr.create_window(
        str(tmp_path), window_name="ccgram-status-itest", start_agent=False
    )
    assert ok is True
    try:
        status = await herdr.agent_status(window_id)
        assert status is not None
        assert status.state in {"working", "idle", "done", "blocked", "unknown"}
    finally:
        assert await herdr.kill_window(window_id) is True


async def test_watch_events_streams_window_death(herdr: HerdrManager, tmp_path) -> None:
    ok, _msg, _name, window_id = await herdr.create_window(
        str(tmp_path), window_name="ccgram-evt-itest", start_agent=False
    )
    assert ok is True

    seen: list = []

    async def consume() -> None:
        async for event in herdr.watch_events([window_id]):
            seen.append(event)
            if event.kind == "window_died" and event.window_id == window_id:
                return

    task = asyncio.create_task(consume())
    try:
        await asyncio.sleep(0.5)
        assert await herdr.kill_window(window_id) is True
        await asyncio.wait_for(task, timeout=8.0)
    except TimeoutError:
        task.cancel()
        raise AssertionError(f"no window_died event; saw {seen}") from None
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    assert any(e.kind == "window_died" and e.window_id == window_id for e in seen)


async def test_split_window_adds_pane(herdr: HerdrManager, tmp_path) -> None:
    ok, _msg, _name, window_id = await herdr.create_window(
        str(tmp_path), window_name="ccgram-split-itest", start_agent=False
    )
    assert ok is True
    try:
        before = await herdr.list_panes(window_id)
        new_pane = await herdr.split_window(window_id)
        assert new_pane is not None
        assert new_pane != window_id
        after = await herdr.list_panes(window_id)
        assert len(after) == len(before) + 1
        assert any(p.pane_id == new_pane for p in after)
    finally:
        assert await herdr.kill_window(window_id) is True


async def test_list_windows_tab_identity(herdr: HerdrManager, tmp_path) -> None:
    ok, _msg, tab_label, window_id = await herdr.create_window(
        str(tmp_path), window_name="ccgram-list-itest", start_agent=False
    )
    assert ok is True
    try:
        refs = await herdr.list_windows()
        found = [r for r in refs if r.window_id == window_id]
        assert found, (
            f"tab {window_id!r} not found in list_windows; got {[r.window_id for r in refs]}"
        )
        ref = found[0]
        assert ref.window_id == window_id
        assert tab_label in ref.window_name or TOPIC_PREFIX_SEPARATOR in ref.window_name
    finally:
        await herdr.kill_window(window_id)


async def test_list_windows_internal_label_hidden(herdr: HerdrManager) -> None:
    refs = await herdr.list_windows()
    for ref in refs:
        parts = ref.window_name.split(TOPIC_PREFIX_SEPARATOR)
        for part in parts:
            assert not (part.startswith("__") and part.endswith("__")), (
                f"Internal label leaked into list_windows: {ref.window_name!r}"
            )


async def test_rename_window(herdr: HerdrManager, tmp_path) -> None:
    ok, _msg, _name, window_id = await herdr.create_window(
        str(tmp_path), window_name="ccgram-rename-before", start_agent=False
    )
    assert ok is True
    try:
        assert await herdr.rename_window(window_id, "ccgram-rename-after") is True
        ref = await herdr.find_window_by_id(window_id)
        assert ref is not None
        assert "ccgram-rename-after" in ref.window_name
    finally:
        await herdr.kill_window(window_id)


async def test_kill_window_closes_tab(herdr: HerdrManager, tmp_path) -> None:
    ok, _msg, _name, window_id = await herdr.create_window(
        str(tmp_path), window_name="ccgram-kill-itest", start_agent=False
    )
    assert ok is True
    assert await herdr.kill_window(window_id) is True
    await asyncio.sleep(0.3)
    assert await herdr.find_window_by_id(window_id) is None


def test_resolve_by_session_id_maps_old_tab_to_new() -> None:
    from ccgram.window_resolver import LiveWindow, resolve_stale_ids
    from ccgram.window_state_store import WindowState

    old_id = "w1:t1"
    new_id = "w2:t1"
    session_id = "session-uuid-aabbcc"

    live_windows = [LiveWindow(window_id=new_id, window_name="ccgram ▸ main")]
    live_session_ids = {new_id: session_id}

    window_states: dict = {old_id: WindowState(session_id=session_id)}
    thread_bindings = {1: {42: old_id}}
    user_window_offsets: dict = {}
    window_display_names = {old_id: "ccgram ▸ main"}

    changed = resolve_stale_ids(
        live_windows,
        window_states,
        thread_bindings,
        user_window_offsets,
        window_display_names,
        ids_stable=False,
        live_session_ids=live_session_ids,
    )

    assert changed is True
    assert thread_bindings[1][42] == new_id
    assert new_id in window_states
    assert old_id not in window_states


async def test_scrollback_clamps_to_capability(herdr: HerdrManager, tmp_path) -> None:
    ok, _msg, _name, window_id = await herdr.create_window(
        str(tmp_path), window_name="ccgram-itest-scroll", start_agent=False
    )
    assert ok is True
    try:
        result = await herdr.capture_scrollback(window_id, lines=5000)
        if result is not None:
            assert result.truncated is True
    finally:
        await herdr.kill_window(window_id)
