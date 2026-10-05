from collections.abc import Callable

import structlog

logger = structlog.get_logger()

_VALID_SCOPES = frozenset({"topic", "window", "qualified", "chat"})


class TopicStateRegistry:
    def __init__(self) -> None:
        self._cleanups: dict[str, list[Callable[..., None]]] = {
            s: [] for s in _VALID_SCOPES
        }

    def register(
        self, scope: str
    ) -> Callable[[Callable[..., None]], Callable[..., None]]:
        if scope not in _VALID_SCOPES:
            msg = f"Unknown cleanup scope {scope!r}; valid: {sorted(_VALID_SCOPES)}"
            raise ValueError(msg)

        def decorator(fn: Callable) -> Callable:
            bucket = self._cleanups[scope]
            if fn not in bucket:
                bucket.append(fn)
            return fn

        return decorator

    def register_bound(self, scope: str, method: Callable[..., None]) -> None:
        if scope not in _VALID_SCOPES:
            msg = f"Unknown cleanup scope {scope!r}; valid: {sorted(_VALID_SCOPES)}"
            raise ValueError(msg)
        bucket = self._cleanups[scope]
        if method not in bucket:
            bucket.append(method)

    def _reset_for_testing(self) -> None:
        for bucket in self._cleanups.values():
            bucket.clear()

    def clear_topic(self, user_id: int, thread_id: int) -> None:
        for fn in self._cleanups["topic"]:
            _safe_call(fn, user_id, thread_id)

    def clear_window(self, window_id: str) -> None:
        for fn in self._cleanups["window"]:
            _safe_call(fn, window_id)

    def clear_qualified(self, qualified_id: str) -> None:
        for fn in self._cleanups["qualified"]:
            _safe_call(fn, qualified_id)

    def clear_chat(self, chat_id: int, thread_id: int) -> None:
        for fn in self._cleanups["chat"]:
            _safe_call(fn, chat_id, thread_id)

    def clear_all(
        self,
        user_id: int,
        thread_id: int,
        *,
        window_id: str | None = None,
        qualified_id: str | None = None,
        chat_id: int | None = None,
    ) -> None:
        self.clear_topic(user_id, thread_id)
        if chat_id is not None:
            self.clear_chat(chat_id, thread_id)
        if window_id:
            self.clear_window(window_id)
        if qualified_id:
            self.clear_qualified(qualified_id)


def _safe_call(fn: Callable, *args: object) -> None:
    try:
        fn(*args)
    except (
        OSError,
        ValueError,
        LookupError,
        TypeError,
        RuntimeError,
        AttributeError,
        ImportError,
    ):
        name = getattr(fn, "__qualname__", repr(fn))
        logger.warning("cleanup_function_failed", fn=name, exc_info=True)


topic_state = TopicStateRegistry()
