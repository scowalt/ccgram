import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

MessageRole = Literal["user", "assistant"]
ContentType = Literal["text", "thinking", "tool_use", "tool_result", "local_command"]

RESUME_ID_RE = re.compile(r"^[\w-]+$")

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass(frozen=True, slots=True)
class SessionStartEvent:
    session_id: str
    cwd: str
    transcript_path: str
    window_key: str


@dataclass(frozen=True, slots=True)
class AgentMessage:
    text: str
    role: MessageRole
    content_type: ContentType
    is_complete: bool = True
    phase: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    raw_text: str
    display_label: str
    is_interactive: bool = False
    ui_type: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredCommand:
    name: str
    description: str
    source: Literal["builtin", "skill", "command"]


@dataclass(frozen=True, slots=True)
class HookEvent:
    event_type: str
    window_key: str
    session_id: str
    data: dict[str, Any]
    timestamp: float


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    name: str
    launch_command: str
    supports_hook: bool = False
    hook_install_managed_by_ccgram: bool = False
    supports_resume: bool = False
    supports_continue: bool = False
    supports_structured_transcript: bool = False
    supports_incremental_read: bool = True
    uses_pane_title: bool = False
    uses_pyte_status_parsing: bool = False
    builtin_commands: tuple[str, ...] = ()
    supports_user_command_discovery: bool = False
    supports_status_snapshot: bool = False
    chat_first_command_path: bool = False
    has_yolo_confirmation: bool = False
    supports_task_tracking: bool = False
    tui_picker_commands: frozenset[str] = frozenset()


class AgentProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,
    ) -> str: ...

    def parse_transcript_line(self, line: str) -> dict[str, Any] | None: ...

    def read_transcript_file(
        self, file_path: str, last_offset: int
    ) -> tuple[list[dict[str, Any]], int]: ...

    def parse_transcript_entries(
        self,
        entries: list[dict[str, Any]],
        pending_tools: dict[str, Any],
        cwd: str | None = None,
    ) -> tuple[list[AgentMessage], dict[str, Any]]: ...

    def parse_terminal_status(
        self, pane_text: str, *, pane_title: str = ""
    ) -> StatusUpdate | None: ...

    def extract_bash_output(self, pane_text: str, command: str) -> str | None: ...

    def is_user_transcript_entry(self, entry: dict[str, Any]) -> bool: ...

    def parse_history_entry(self, entry: dict[str, Any]) -> AgentMessage | None: ...

    def discover_transcript(
        self,
        cwd: str,
        window_key: str,
        *,
        max_age: float | None = None,
        exclude_session_ids: set[str] | None = None,
        exclude_transcript_paths: set[str] | None = None,
    ) -> SessionStartEvent | None: ...

    def requires_pane_title_for_detection(self, pane_current_command: str) -> bool: ...

    def detect_from_pane_title(
        self, pane_current_command: str, pane_title: str
    ) -> bool: ...

    def discover_commands(self, base_dir: str) -> list[DiscoveredCommand]: ...

    def build_status_snapshot(
        self,
        transcript_path: str,
        *,
        display_name: str,
        session_id: str = "",
        cwd: str = "",
    ) -> str | None: ...

    def has_output_since(self, transcript_path: str, offset: int) -> bool: ...

    async def scrape_current_mode(self, window_id: str) -> str | None:  # noqa: ARG002
        return None

    async def seed_task_state(  # noqa: ARG002
        self,
        window_id: str,
        session_id: str,
        transcript_path: str,
    ) -> None:
        pass

    def apply_task_entries(  # noqa: ARG002
        self,
        window_id: str,
        session_id: str,
        entries: list[dict],
    ) -> None:
        pass
