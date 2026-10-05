import json
import structlog
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .utils import atomic_write_json

logger = structlog.get_logger()


@dataclass
class TrackedSession:
    session_id: str
    file_path: str
    last_byte_offset: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrackedSession":
        return cls(
            session_id=data.get("session_id", ""),
            file_path=data.get("file_path", ""),
            last_byte_offset=data.get("last_byte_offset", 0),
        )


@dataclass
class MonitorState:
    state_file: Path
    tracked_sessions: dict[str, TrackedSession] = field(default_factory=dict)
    events_offset: int = 0
    _dirty: bool = field(default=False, repr=False)

    def load(self) -> None:
        if not self.state_file.exists():
            logger.debug("State file does not exist: %s", self.state_file)
            return

        try:
            data = json.loads(self.state_file.read_text())
            sessions = data.get("tracked_sessions", {})
            self.tracked_sessions = {
                k: TrackedSession.from_dict(v) for k, v in sessions.items()
            }
            self.events_offset = data.get("events_offset", 0)
            logger.info(
                "Loaded %d tracked sessions from state", len(self.tracked_sessions)
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to load state file: %s", e)
            self.tracked_sessions = {}

    def save(self) -> None:
        data = {
            "tracked_sessions": {
                k: v.to_dict() for k, v in self.tracked_sessions.items()
            },
            "events_offset": self.events_offset,
        }

        try:
            atomic_write_json(self.state_file, data)
            self._dirty = False
        except OSError:
            logger.exception("Failed to save state file")

    def get_session(self, session_id: str) -> TrackedSession | None:
        return self.tracked_sessions.get(session_id)

    def update_session(self, session: TrackedSession) -> None:
        self.tracked_sessions[session.session_id] = session
        self._dirty = True

    def remove_session(self, session_id: str) -> None:
        if session_id in self.tracked_sessions:
            del self.tracked_sessions[session_id]
            self._dirty = True

    def save_if_dirty(self) -> None:
        if self._dirty:
            self.save()
