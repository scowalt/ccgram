import time

import structlog
from telegram.error import BadRequest, TelegramError

from ...config import config
from ...telegram_client import TelegramClient
from ...thread_router import thread_router
from ...topic_state_registry import topic_state
from ...window_query import get_approval_mode

logger = structlog.get_logger()

EMOJI_GREEN_CIRCLE = "\U0001f7e2"
EMOJI_YELLOW_CIRCLE = "\U0001f7e1"
EMOJI_DONE = "\u2705"
EMOJI_DEAD = "\U0001f4a5"
EMOJI_YOLO = "\U0001f3b2"
EMOJI_RC = "\U0001f4e1"
_EMOJI_DEAD_OLD = (
    "\u26ab",
    "\u274c",
)

EMOJI_ACTIVE = EMOJI_GREEN_CIRCLE
EMOJI_IDLE = EMOJI_YELLOW_CIRCLE

_STATE_EMOJI_SYSTEM: dict[str, str] = {
    "active": EMOJI_GREEN_CIRCLE,
    "idle": EMOJI_YELLOW_CIRCLE,
    "done": EMOJI_DONE,
    "dead": EMOJI_DEAD,
}
_STATE_EMOJI_USER: dict[str, str] = {
    "active": EMOJI_YELLOW_CIRCLE,
    "idle": EMOJI_GREEN_CIRCLE,
    "done": EMOJI_DONE,
    "dead": EMOJI_DEAD,
}


def _state_emoji_map() -> dict[str, str]:
    return _STATE_EMOJI_USER if config.status_mode == "user" else _STATE_EMOJI_SYSTEM


_VALID_TOPIC_STATES = frozenset({"active", "idle", "done", "dead"})


def _display_state_for_update(state: str) -> str:
    if state in _VALID_TOPIC_STATES:
        return ""
    return state


DEBOUNCE_TO_ACTIVE_SECONDS = 5.0
DEBOUNCE_TO_IDLE_SECONDS = 30.0
DEBOUNCE_TERMINAL_SECONDS = 5.0

_DEBOUNCE_BY_STATE: dict[str, float] = {
    "active": DEBOUNCE_TO_ACTIVE_SECONDS,
    "idle": DEBOUNCE_TO_IDLE_SECONDS,
    "done": DEBOUNCE_TERMINAL_SECONDS,
    "dead": DEBOUNCE_TERMINAL_SECONDS,
}

_topic_states: dict[tuple[int, int], tuple[str, str, bool]] = {}

_pending_transitions: dict[tuple[int, int], tuple[str, float]] = {}

_topic_names: dict[tuple[int, int], str] = {}

_disabled_chats: set[int] = set()


def _resolve_topic_name(key: tuple[int, int], display_name: str) -> tuple[str, bool]:
    clean = strip_emoji_prefix(display_name)
    cached = _topic_names.get(key)
    if cached is None:
        _topic_names[key] = clean
        return clean, True
    if cached != clean:
        _topic_names[key] = clean
        return clean, True
    return cached, False


def _should_apply_update(
    key: tuple[int, int],
    state: str,
    state_token: tuple[str, str, bool],
    *,
    name_changed: bool,
    now: float,
) -> bool:
    if _topic_states.get(key) == state_token:
        _pending_transitions.pop(key, None)
        return name_changed

    pending = _pending_transitions.get(key)
    if pending is None or pending[0] != state:
        _pending_transitions[key] = (state, now)
        return False

    debounce = _DEBOUNCE_BY_STATE.get(state, DEBOUNCE_TO_IDLE_SECONDS)
    if now - pending[1] < debounce:
        return False

    _pending_transitions.pop(key, None)
    return True


def _compose_topic_name(
    clean_name: str,
    *,
    state: str = "",
    approval_mode: str = "normal",
    rc_active: bool = False,
) -> str:
    del state, approval_mode, rc_active
    return clean_name


async def _edit_topic_name(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    key: tuple[int, int],
    new_name: str,
    *,
    state_token: tuple[str, str, bool] | None = None,
    state: str = "",
) -> None:
    try:
        await client.edit_forum_topic(
            chat_id=chat_id,
            message_thread_id=thread_id,
            name=new_name,
        )
        if state_token is not None:
            _topic_states[key] = state_token
        logger.debug(
            "Updated topic emoji: chat=%d thread=%d state=%s name='%s'",
            chat_id,
            thread_id,
            state or "sync",
            new_name,
        )
    except BadRequest as e:
        if "Not enough rights" in e.message:
            _disabled_chats.add(chat_id)
            logger.info(
                "Topic emoji disabled for chat %d: insufficient permissions",
                chat_id,
            )
        elif (
            "topic_not_modified" in e.message.lower() or "Topic_id_invalid" in e.message
        ):
            if state_token is not None:
                _topic_states[key] = state_token
        else:
            logger.debug("Failed to update topic emoji: %s", e)
    except TelegramError:
        pass


def _resolve_approval_mode(chat_id: int, thread_id: int) -> str:
    window_id = thread_router.get_window_for_chat_thread(chat_id, thread_id)
    if not window_id:
        return "normal"
    return get_approval_mode(window_id)


def _resolve_rc_mode(chat_id: int, thread_id: int) -> bool:
    window_id = thread_router.get_window_for_chat_thread(chat_id, thread_id)
    if not window_id:
        return False
    from ..polling.polling_state import terminal_screen_buffer

    return terminal_screen_buffer.is_rc_active(window_id)


def format_topic_name_for_mode(display_name: str, approval_mode: str) -> str:
    clean_name = strip_emoji_prefix(display_name)
    return _compose_topic_name(clean_name, approval_mode=approval_mode)


async def sync_topic_name(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    display_name: str,
) -> None:
    if chat_id in _disabled_chats:
        return

    key = (chat_id, thread_id)
    clean_name, _ = _resolve_topic_name(key, display_name)
    approval_mode = _resolve_approval_mode(chat_id, thread_id)
    rc_active = _resolve_rc_mode(chat_id, thread_id)
    cached = _topic_states.get(key)
    state = cached[0] if cached else ""
    state_token = (state, approval_mode, rc_active) if cached else None
    new_name = _compose_topic_name(
        clean_name,
        state=state,
        approval_mode=approval_mode,
        rc_active=rc_active,
    )
    await _edit_topic_name(
        client,
        chat_id,
        thread_id,
        key,
        new_name,
        state_token=state_token,
        state=state,
    )


async def update_topic_emoji(
    client: TelegramClient,
    chat_id: int,
    thread_id: int,
    state: str,
    display_name: str,
) -> None:
    if chat_id in _disabled_chats:
        return

    key = (chat_id, thread_id)
    clean_name, name_changed = _resolve_topic_name(key, display_name)

    if state not in _VALID_TOPIC_STATES:
        return

    approval_mode = _resolve_approval_mode(chat_id, thread_id)
    rc_active = _resolve_rc_mode(chat_id, thread_id)
    display_state = _display_state_for_update(state)
    state_token = (display_state, approval_mode, rc_active)

    emoji = _state_emoji_map().get(display_state, "")
    if display_state and not emoji:
        return

    now = time.monotonic()
    if not _should_apply_update(
        key,
        state,
        state_token,
        name_changed=name_changed,
        now=now,
    ):
        return

    new_name = _compose_topic_name(
        clean_name,
        state=display_state,
        approval_mode=approval_mode,
        rc_active=rc_active,
    )
    await _edit_topic_name(
        client,
        chat_id,
        thread_id,
        key,
        new_name,
        state_token=state_token,
        state=state,
    )


def strip_emoji_prefix(name: str) -> str:
    for emoji in (EMOJI_ACTIVE, EMOJI_IDLE, EMOJI_DONE, EMOJI_DEAD, *_EMOJI_DEAD_OLD):
        prefix = f"{emoji} "
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    for badge in (EMOJI_RC, EMOJI_YOLO):
        badge_prefix = f"{badge} "
        if name.startswith(badge_prefix):
            name = name[len(badge_prefix) :]
    return name


def update_stored_topic_name(chat_id: int, thread_id: int, new_clean_name: str) -> None:
    _topic_names[(chat_id, thread_id)] = new_clean_name


@topic_state.register("chat")
def clear_topic_emoji_state(chat_id: int, thread_id: int) -> None:
    key = (chat_id, thread_id)
    _topic_states.pop(key, None)
    _pending_transitions.pop(key, None)
    _topic_names.pop(key, None)


_MAX_DISABLED_CHATS = 1000


@topic_state.register("chat")
def clear_disabled_chat(chat_id: int, _thread_id: int = 0) -> None:
    _disabled_chats.discard(chat_id)
    if len(_disabled_chats) > _MAX_DISABLED_CHATS:
        _disabled_chats.clear()


def reset_all_state() -> None:
    _topic_states.clear()
    _pending_transitions.clear()
    _disabled_chats.clear()
    _topic_names.clear()
