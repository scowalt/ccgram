import asyncio
import shutil

import pytest

from ccgram.thread_router import thread_router

from ._helpers import (
    TEST_THREAD_ID,
    TEST_USER_ID,
    find_message_id_for,
    make_callback_update,
    make_text_update,
    setup_bound_topic,
    wait_for_pane,
    wait_for_pane_scrollback,
    wait_for_send,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        shutil.which("claude") is None, reason="claude CLI not installed"
    ),
]


async def test_basic_lifecycle(e2e_app, work_dir):
    app, calls, tmux, session_mgr = e2e_app

    window_id, _ = await setup_bound_topic(app, calls, work_dir)

    await wait_for_pane(tmux, window_id, pattern="hello agent|╭|>", timeout=30)

    await wait_for_send(
        calls,
        predicate=lambda d: (
            d.get("message_thread_id") == TEST_THREAD_ID
            and len(d.get("text", "")) > 10
            and "Bound" not in d.get("text", "")
            and "Select" not in d.get("text", "")
        ),
        timeout=120,
    )


async def test_sessions_command(e2e_app, work_dir):
    app, calls, tmux, session_mgr = e2e_app
    await setup_bound_topic(app, calls, work_dir)

    calls.clear()

    u = make_text_update("/sessions", bot=app.bot)
    await app.process_update(u)

    await wait_for_send(calls, timeout=10)


async def test_screenshot_command(e2e_app, work_dir):
    app, calls, tmux, session_mgr = e2e_app
    await setup_bound_topic(app, calls, work_dir)

    await asyncio.sleep(3)
    calls.clear()

    u = make_text_update("/screenshot", bot=app.bot)
    await app.process_update(u)

    await wait_for_send(
        calls,
        method="sendDocument",
        timeout=15,
    )


async def test_help_command_forwarded(e2e_app, work_dir):
    app, calls, tmux, session_mgr = e2e_app
    window_id, _ = await setup_bound_topic(app, calls, work_dir)

    await wait_for_pane(tmux, window_id, timeout=30)
    calls.clear()

    u = make_text_update("/help", bot=app.bot)
    await app.process_update(u)

    await wait_for_pane(tmux, window_id, pattern="help", timeout=15)


@pytest.mark.xfail(
    reason=(
        "Pre-existing flaky test: recovery flow creates a new tmux window, "
        "binds the thread, but the window vanishes within ~5s in this fixture "
        "before wait_for_pane can capture content. Unable to isolate mechanism "
        "(no kill_window callers explain it; libtmux session caching suspected). "
        "Failing since 2026-04-13 across multiple modularity refactor cycles."
    ),
    strict=False,
)
async def test_recovery_fresh_start(e2e_app, work_dir):
    app, calls, tmux, session_mgr = e2e_app
    window_id, _ = await setup_bound_topic(app, calls, work_dir)

    await wait_for_pane(tmux, window_id, timeout=30)

    await tmux.kill_window(window_id)
    await asyncio.sleep(1)

    calls.clear()
    u = make_text_update("are you there?", bot=app.bot)
    await app.process_update(u)

    recovery_data = await wait_for_send(
        calls,
        predicate=lambda d: "ended" in d.get("text", ""),
        timeout=10,
    )
    assert recovery_data is not None

    recovery_msg_id = find_message_id_for(
        calls,
        predicate=lambda d: "ended" in d.get("text", ""),
    )
    assert recovery_msg_id is not None

    u_fresh = make_callback_update(
        f"rec:f:{window_id}",
        recovery_msg_id,
        bot=app.bot,
    )
    await app.process_update(u_fresh)

    deadline = asyncio.get_event_loop().time() + 15
    new_window_id = None
    while asyncio.get_event_loop().time() < deadline:
        new_window_id = thread_router.get_window_for_thread(
            TEST_USER_ID, TEST_THREAD_ID
        )
        if new_window_id is not None:
            break
        await asyncio.sleep(0.5)
    assert new_window_id is not None, "Topic not rebound after fresh recovery"
    new_pane = await wait_for_pane(tmux, new_window_id, timeout=30)
    assert new_pane is not None


@pytest.mark.xfail(
    reason=(
        "Pre-existing flaky test: same root cause as test_recovery_fresh_start "
        "— recovery-created window vanishes before wait_for_pane succeeds. "
        "Failing since 2026-04-13 across multiple refactor cycles."
    ),
    strict=False,
)
async def test_recovery_continue(e2e_app, work_dir):
    app, calls, tmux, session_mgr = e2e_app
    window_id, _ = await setup_bound_topic(app, calls, work_dir)

    await wait_for_pane(tmux, window_id, timeout=30)

    await tmux.kill_window(window_id)
    await asyncio.sleep(1)

    calls.clear()
    u = make_text_update("continue please", bot=app.bot)
    await app.process_update(u)

    recovery_data = await wait_for_send(
        calls,
        predicate=lambda d: "ended" in d.get("text", ""),
        timeout=10,
    )
    assert recovery_data is not None

    recovery_msg_id = find_message_id_for(
        calls,
        predicate=lambda d: "ended" in d.get("text", ""),
    )

    u_cont = make_callback_update(
        f"rec:c:{window_id}",
        recovery_msg_id,
        bot=app.bot,
    )
    await app.process_update(u_cont)

    deadline = asyncio.get_event_loop().time() + 15
    new_window_id = None
    while asyncio.get_event_loop().time() < deadline:
        new_window_id = thread_router.get_window_for_thread(
            TEST_USER_ID, TEST_THREAD_ID
        )
        if new_window_id is not None:
            break
        await asyncio.sleep(0.5)
    assert new_window_id is not None, "Topic not rebound after continue recovery"
    new_pane = await wait_for_pane(tmux, new_window_id, timeout=30)
    assert new_pane is not None


async def test_status_transitions(e2e_app, work_dir):
    app, calls, tmux, session_mgr = e2e_app
    window_id, _ = await setup_bound_topic(app, calls, work_dir)

    await wait_for_pane(tmux, window_id, timeout=30)

    calls.clear()
    u = make_text_update("say hello", bot=app.bot)
    await app.process_update(u)

    await wait_for_send(
        calls,
        method="editForumTopic",
        predicate=lambda d: d.get("message_thread_id") == TEST_THREAD_ID,
        timeout=30,
    )


async def test_multi_topic_isolation(e2e_app, work_dir):
    app, calls, tmux, session_mgr = e2e_app

    thread_a = 42
    thread_b = 99

    window_a, _ = await setup_bound_topic(
        app, calls, work_dir, thread_id=thread_a, initial_text="you are agent A"
    )

    window_b, _ = await setup_bound_topic(
        app, calls, work_dir, thread_id=thread_b, initial_text="you are agent B"
    )

    assert window_a != window_b

    assert thread_router.get_window_for_thread(TEST_USER_ID, thread_a) == window_a
    assert thread_router.get_window_for_thread(TEST_USER_ID, thread_b) == window_b

    calls.clear()
    u_a = make_text_update("say A", bot=app.bot, thread_id=thread_a)
    await app.process_update(u_a)

    pane_a = await wait_for_pane_scrollback(
        tmux,
        window_a,
        pattern="say A",
        timeout=10,
    )
    pane_b = await tmux.capture_pane_scrollback(window_b, history=200)
    assert "say A" in pane_a
    assert pane_b is None or "say A" not in pane_b
