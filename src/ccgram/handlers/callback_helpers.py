from telegram import Update

from ..thread_router import thread_router
from .callback_data import CB_PANE_DELIMITER


def user_owns_window(user_id: int, window_id: str) -> bool:
    return window_id in thread_router.get_all_thread_windows(user_id).values()


def parse_target(target: str) -> tuple[str, str | None]:
    if CB_PANE_DELIMITER in target:
        idx = target.index(CB_PANE_DELIMITER)
        return target[:idx], target[idx + 1 :]
    return target, None


def get_thread_id(update: Update) -> int | None:
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg is None:
        return None
    tid = getattr(msg, "message_thread_id", None)
    if tid is None or tid == 1:
        return None
    return tid
