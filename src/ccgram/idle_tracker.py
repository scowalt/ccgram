import time


class IdleTracker:
    def __init__(self) -> None:
        self._last_activity: dict[str, float] = {}

    def record_activity(self, session_id: str, ts: float | None = None) -> None:
        self._last_activity[session_id] = ts if ts is not None else time.monotonic()

    def get_last_activity(self, session_id: str) -> float | None:
        return self._last_activity.get(session_id)

    def clear_session(self, session_id: str) -> None:
        self._last_activity.pop(session_id, None)
