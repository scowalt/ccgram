from __future__ import annotations

import structlog
from collections.abc import Callable, Iterator
from typing import Any, cast

logger = structlog.get_logger()


class ThreadRouter:
    def __init__(
        self,
        *,
        schedule_save: Callable[[], None],
        has_window_state: Callable[[str], bool],
    ) -> None:
        self.thread_bindings: dict[int, dict[int, str]] = {}
        self.group_chat_ids: dict[str, int] = {}
        self.window_display_names: dict[str, str] = {}
        self._window_to_thread: dict[tuple[int, str], int] = {}
        self._schedule_save: Callable[[], None] = schedule_save
        self._has_window_state: Callable[[str], bool] = has_window_state

    def reset(self) -> None:
        self.thread_bindings.clear()
        self.group_chat_ids.clear()
        self.window_display_names.clear()
        self._window_to_thread.clear()

    def _rebuild_reverse_index(self) -> None:
        self._window_to_thread = {}
        for uid, bindings in self.thread_bindings.items():
            for tid, wid in bindings.items():
                self._window_to_thread[(uid, wid)] = tid

    def _dedup_thread_bindings(self) -> None:
        for _uid, bindings in self.thread_bindings.items():
            window_threads: dict[str, list[int]] = {}
            for tid, wid in bindings.items():
                window_threads.setdefault(wid, []).append(tid)
            for wid, tids in window_threads.items():
                if len(tids) > 1:
                    keep = max(tids)
                    for tid in tids:
                        if tid != keep:
                            del bindings[tid]
                            logger.warning(
                                "Startup: removed duplicate binding "
                                "thread %d -> window %s (keeping %d)",
                                tid,
                                wid,
                                keep,
                            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_bindings": {
                str(uid): {str(tid): wid for tid, wid in bindings.items()}
                for uid, bindings in self.thread_bindings.items()
            },
            "group_chat_ids": self.group_chat_ids,
            "window_display_names": self.window_display_names,
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        self.thread_bindings = {
            int(uid): {int(tid): wid for tid, wid in bindings.items()}
            for uid, bindings in data.get("thread_bindings", {}).items()
        }
        self.group_chat_ids = data.get("group_chat_ids", {})
        self.window_display_names = data.get("window_display_names", {})
        self._dedup_thread_bindings()
        self._rebuild_reverse_index()

    def bind_thread(
        self, user_id: int, thread_id: int, window_id: str, window_name: str = ""
    ) -> None:
        if user_id not in self.thread_bindings:
            self.thread_bindings[user_id] = {}

        stale = [
            tid
            for tid, wid in self.thread_bindings[user_id].items()
            if wid == window_id and tid != thread_id
        ]
        for tid in stale:
            del self.thread_bindings[user_id][tid]
            logger.info(
                "Evicted stale binding: thread %d -> window_id %s "
                "(replaced by thread %d)",
                tid,
                window_id,
                thread_id,
            )

        old_window = self.thread_bindings[user_id].get(thread_id)
        if old_window is not None and old_window != window_id:
            self._window_to_thread.pop((user_id, old_window), None)

        self.thread_bindings[user_id][thread_id] = window_id
        self._window_to_thread[(user_id, window_id)] = thread_id
        if window_name:
            self.window_display_names[window_id] = window_name
        self._schedule_save()
        display = window_name or self.get_display_name(window_id)
        logger.info(
            "Bound thread %d -> window_id %s (%s) for user %d",
            thread_id,
            window_id,
            display,
            user_id,
        )

    def unbind_thread(self, user_id: int, thread_id: int) -> str | None:
        bindings = self.thread_bindings.get(user_id)
        if not bindings or thread_id not in bindings:
            return None
        window_id = bindings.pop(thread_id)
        self._window_to_thread.pop((user_id, window_id), None)
        if not bindings:
            del self.thread_bindings[user_id]
        logger.info(
            "Unbound thread %d (was %s) for user %d",
            thread_id,
            window_id,
            user_id,
        )

        chat_key = f"{user_id}:{thread_id}"
        self.group_chat_ids.pop(chat_key, None)

        still_bound = any(
            wid == window_id
            for ub in self.thread_bindings.values()
            for wid in ub.values()
        )
        if not still_bound and not self._has_window_state(window_id):
            self.window_display_names.pop(window_id, None)

        self._schedule_save()
        return window_id

    def get_window_for_thread(self, user_id: int, thread_id: int) -> str | None:
        bindings = self.thread_bindings.get(user_id)
        if not bindings:
            return None
        return bindings.get(thread_id)

    def get_thread_for_window(self, user_id: int, window_id: str) -> int | None:
        return self._window_to_thread.get((user_id, window_id))

    def get_all_thread_windows(self, user_id: int) -> dict[int, str]:
        return dict(self.thread_bindings.get(user_id, {}))

    def resolve_window_for_thread(
        self,
        user_id: int,
        thread_id: int | None,
    ) -> str | None:
        if thread_id is None:
            return None
        return self.get_window_for_thread(user_id, thread_id)

    def has_window(self, window_id: str) -> bool:
        return any(wid == window_id for (_, wid) in self._window_to_thread)

    def iter_thread_bindings(self) -> Iterator[tuple[int, int, str]]:
        for user_id, bindings in self.thread_bindings.items():
            for thread_id, window_id in bindings.items():
                yield user_id, thread_id, window_id

    def set_group_chat_id(self, user_id: int, thread_id: int, chat_id: int) -> None:
        key = f"{user_id}:{thread_id}"
        if self.group_chat_ids.get(key) != chat_id:
            self.group_chat_ids[key] = chat_id
            self._schedule_save()
            logger.debug(
                "Stored group chat_id %d for user %d, thread %d",
                chat_id,
                user_id,
                thread_id,
            )

    def resolve_chat_id(self, user_id: int, thread_id: int | None = None) -> int:
        if thread_id is not None:
            key = f"{user_id}:{thread_id}"
            group_id = self.group_chat_ids.get(key)
            if group_id is not None:
                return group_id
        return user_id

    def get_window_for_chat_thread(self, chat_id: int, thread_id: int) -> str | None:
        for user_id, bindings in self.thread_bindings.items():
            window_id = bindings.get(thread_id)
            if not window_id:
                continue
            key = f"{user_id}:{thread_id}"
            resolved_chat = self.group_chat_ids.get(key, user_id)
            if resolved_chat == chat_id:
                return window_id
        return None

    def get_display_name(self, window_id: str) -> str:
        return self.window_display_names.get(window_id, window_id)

    def pop_display_name(self, window_id: str) -> str:
        if window_id not in self.window_display_names:
            return window_id
        name = self.window_display_names.pop(window_id)
        self._schedule_save()
        return name

    def set_display_name(self, window_id: str, window_name: str) -> None:
        if self.window_display_names.get(window_id) != window_name:
            self.window_display_names[window_id] = window_name
            self._schedule_save()

    def sync_display_names(self, live_windows: list[tuple[str, str]]) -> bool:
        changed = False
        for window_id, window_name in live_windows:
            old = self.window_display_names.get(window_id)
            if old and old != window_name:
                self.window_display_names[window_id] = window_name
                changed = True
                logger.debug(
                    "Synced display name: %s %s → %s", window_id, old, window_name
                )
        if changed:
            self._schedule_save()
        return changed


_active_router: ThreadRouter | None = None


def get_thread_router() -> ThreadRouter:
    if _active_router is None:
        raise RuntimeError(
            "ThreadRouter not yet wired. "
            "Instantiate SessionManager() before accessing thread_router."
        )
    return _active_router


def install_thread_router(router: ThreadRouter) -> None:
    global _active_router
    _active_router = router


class _ThreadRouterProxy:
    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(get_thread_router(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(get_thread_router(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(get_thread_router(), name)

    def __repr__(self) -> str:
        if _active_router is None:
            return "<ThreadRouterProxy unwired>"
        return f"<ThreadRouterProxy → {_active_router!r}>"


thread_router: ThreadRouter = cast("ThreadRouter", _ThreadRouterProxy())
