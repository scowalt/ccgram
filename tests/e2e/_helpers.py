import asyncio
import re
from datetime import datetime

from telegram import (
    CallbackQuery,
    Chat,
    Message,
    MessageEntity,
    Update,
    User,
)

TEST_USER_ID = 12345
TEST_CHAT_ID = -100999
TEST_THREAD_ID = 42

_next_update_id = 1000
_next_message_id = 5000


def _bump_update_id():
    global _next_update_id
    _next_update_id += 1
    return _next_update_id


def _bump_message_id():
    global _next_message_id
    _next_message_id += 1
    return _next_message_id


def make_text_update(
    text,
    *,
    bot=None,
    thread_id=TEST_THREAD_ID,
    user_id=TEST_USER_ID,
    chat_id=TEST_CHAT_ID,
):
    update_id = _bump_update_id()
    user = User(id=user_id, first_name="TestUser", is_bot=False)
    chat = Chat(id=chat_id, type="supergroup")
    entities = None
    if text and text.startswith("/"):
        cmd_end = text.index(" ") if " " in text else len(text)
        entities = [
            MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=cmd_end)
        ]
    message = Message(
        message_id=update_id,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text=text,
        entities=entities,
        message_thread_id=thread_id,
    )
    update = Update(update_id=update_id, message=message)
    if bot:
        update.set_bot(bot)
        message.set_bot(bot)
        for entity in entities or []:
            entity.set_bot(bot)
    return update


def make_callback_update(
    data,
    message_id,
    *,
    bot=None,
    thread_id=TEST_THREAD_ID,
    user_id=TEST_USER_ID,
    chat_id=TEST_CHAT_ID,
):
    update_id = _bump_update_id()
    user = User(id=user_id, first_name="TestUser", is_bot=False)
    chat = Chat(id=chat_id, type="supergroup")
    message = Message(
        message_id=message_id,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text="(callback source)",
        message_thread_id=thread_id,
    )
    callback_query = CallbackQuery(
        id=str(update_id),
        chat_instance="test",
        from_user=user,
        data=data,
        message=message,
    )
    update = Update(update_id=update_id, callback_query=callback_query)
    if bot:
        update.set_bot(bot)
        message.set_bot(bot)
        callback_query.set_bot(bot)
    return update


async def wait_for_send(
    calls,
    *,
    method="sendMessage",
    predicate=None,
    timeout=120.0,
    poll_interval=0.3,
):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        for endpoint, data in calls:
            if endpoint == method and (predicate is None or predicate(data)):
                return data
        await asyncio.sleep(poll_interval)
    raise TimeoutError(
        f"No {method} call matching predicate within {timeout}s "
        f"(total calls: {len(calls)})"
    )


async def wait_for_pane(
    tmux_manager,
    window_id,
    *,
    pattern=None,
    timeout=60.0,
    poll_interval=1.0,
):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        content = await tmux_manager.capture_pane(window_id)
        if content is not None:
            if pattern is None:
                return content
            if isinstance(pattern, str) and re.search(pattern, content):
                return content
        await asyncio.sleep(poll_interval)
    raise TimeoutError(
        f"Pane {window_id} did not match pattern {pattern!r} within {timeout}s"
    )


async def wait_for_pane_scrollback(
    tmux_manager,
    window_id,
    *,
    pattern,
    timeout=60.0,
    poll_interval=1.0,
    history=200,
):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        content = await tmux_manager.capture_pane_scrollback(window_id, history=history)
        if content is not None and re.search(pattern, content):
            return content
        await asyncio.sleep(poll_interval)
    raise TimeoutError(
        f"Pane {window_id} scrollback did not match pattern {pattern!r} "
        f"within {timeout}s"
    )


def find_message_id_for(calls, *, method="sendMessage", predicate=None):
    msg_id_counter = 5000
    for endpoint, data in calls:
        if endpoint in ("sendMessage", "sendPhoto", "sendDocument"):
            msg_id_counter += 1
            if endpoint == method and (predicate is None or predicate(data)):
                return msg_id_counter
    return None


async def setup_bound_topic(
    app,
    calls,
    work_dir,
    *,
    provider="claude",
    thread_id=TEST_THREAD_ID,
    user_id=TEST_USER_ID,
    chat_id=TEST_CHAT_ID,
    initial_text="hello agent",
):
    bot = app.bot

    u1 = make_text_update(
        initial_text,
        bot=bot,
        thread_id=thread_id,
        user_id=user_id,
        chat_id=chat_id,
    )
    await app.process_update(u1)

    await wait_for_send(
        calls,
        predicate=lambda d: "Select" in d.get("text", ""),
        timeout=10.0,
    )

    browser_msg_id = find_message_id_for(
        calls,
        predicate=lambda d: "Select" in d.get("text", ""),
    )
    assert browser_msg_id is not None, "Could not find browser message_id"

    from ccgram.handlers.topics.directory_browser import BROWSE_PATH_KEY

    user_data = app._user_data[user_id]
    user_data[BROWSE_PATH_KEY] = str(work_dir)

    u2 = make_callback_update(
        "db:confirm",
        browser_msg_id,
        bot=bot,
        thread_id=thread_id,
        user_id=user_id,
        chat_id=chat_id,
    )
    await app.process_update(u2)
    await asyncio.sleep(0.5)

    u3 = make_callback_update(
        f"prov:{provider}",
        browser_msg_id,
        bot=bot,
        thread_id=thread_id,
        user_id=user_id,
        chat_id=chat_id,
    )
    await app.process_update(u3)
    await asyncio.sleep(0.5)

    u4 = make_callback_update(
        f"mode:{provider}:normal",
        browser_msg_id,
        bot=bot,
        thread_id=thread_id,
        user_id=user_id,
        chat_id=chat_id,
    )
    await app.process_update(u4)

    await wait_for_send(
        calls,
        method="editMessageText",
        predicate=lambda d: "Bound" in d.get("text", ""),
        timeout=30.0,
    )

    from ccgram.thread_router import thread_router

    window_id = thread_router.get_window_for_thread(user_id, thread_id)
    assert window_id is not None, "Topic not bound after setup flow"

    return window_id, browser_msg_id
