import json
from typing import Any, ClassVar, cast

from ccgram.providers.base import (
    AgentMessage,
    ContentType,
    DiscoveredCommand,
    MessageRole,
    ProviderCapabilities,
    RESUME_ID_RE,
    SessionStartEvent,
    StatusUpdate,
)


def parse_jsonl_line(line: str) -> dict[str, Any] | None:
    if not line or not line.strip():
        return None
    try:
        result = json.loads(line)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def extract_content_blocks(
    content: Any, pending: dict[str, Any]
) -> tuple[str, ContentType, dict[str, Any]]:
    if isinstance(content, str):
        return content, "text", pending
    if not isinstance(content, list):
        return "", "text", pending

    text = ""
    content_type: ContentType = "text"
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            text += block.get("text", "")
        elif btype == "tool_use" and block.get("id"):
            pending[block["id"]] = block.get("name", "unknown")
            content_type = "tool_use"
        elif btype == "tool_result":
            tool_use_id = block.get("tool_use_id")
            if tool_use_id:
                pending.pop(tool_use_id, None)
            content_type = "tool_result"
    return text, content_type, pending


def parse_jsonl_entries(
    entries: list[dict[str, Any]],
    pending_tools: dict[str, Any],
) -> tuple[list[AgentMessage], dict[str, Any]]:
    messages: list[AgentMessage] = []
    pending = dict(pending_tools)

    for entry in entries:
        msg_type = entry.get("type", "")
        if msg_type not in ("user", "assistant"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content", "")
        text, content_type, pending = extract_content_blocks(content, pending)
        if text:
            messages.append(
                AgentMessage(
                    text=text,
                    role=cast(MessageRole, msg_type),
                    content_type=content_type,
                )
            )
    return messages, pending


def extract_bang_output(pane_text: str, command: str) -> str | None:
    if not pane_text or not command:
        return None
    for line in pane_text.splitlines():
        if line.strip().startswith(f"! {command}"):
            return line.strip()
    return None


def is_user_entry(entry: dict[str, Any]) -> bool:
    return entry.get("type") == "user"


def parse_jsonl_history_entry(entry: dict[str, Any]) -> AgentMessage | None:
    msg_type = entry.get("type", "")
    if msg_type not in ("user", "assistant"):
        return None
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content", "")
    if isinstance(content, list):
        text = "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    elif isinstance(content, str):
        text = content
    else:
        text = ""
    if not text:
        return None
    return AgentMessage(
        text=text,
        role=cast(MessageRole, msg_type),
        content_type="text",
    )


class JsonlProvider:
    _CAPS: ClassVar[ProviderCapabilities]
    _BUILTINS: ClassVar[dict[str, str]] = {}

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._CAPS

    def make_launch_args(
        self,
        resume_id: str | None = None,
        use_continue: bool = False,  # noqa: ARG002
    ) -> str:
        if resume_id:
            if not RESUME_ID_RE.match(resume_id):
                raise ValueError(f"Invalid resume_id: {resume_id!r}")
            return f"--resume {resume_id}"
        return ""

    def read_transcript_file(
        self, file_path: str, last_offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        msg = f"{type(self).__name__} uses incremental JSONL reading, not whole-file"
        raise NotImplementedError(msg)

    def parse_transcript_line(self, line: str) -> dict[str, Any] | None:
        return parse_jsonl_line(line)

    def parse_transcript_entries(
        self,
        entries: list[dict[str, Any]],
        pending_tools: dict[str, Any],
        cwd: str | None = None,  # noqa: ARG002
    ) -> tuple[list[AgentMessage], dict[str, Any]]:
        return parse_jsonl_entries(entries, pending_tools)

    def parse_terminal_status(
        self,
        pane_text: str,  # noqa: ARG002
        *,
        pane_title: str = "",  # noqa: ARG002
    ) -> StatusUpdate | None:
        return None

    def extract_bash_output(self, pane_text: str, command: str) -> str | None:
        return extract_bang_output(pane_text, command)

    def is_user_transcript_entry(self, entry: dict[str, Any]) -> bool:
        return is_user_entry(entry)

    def parse_history_entry(self, entry: dict[str, Any]) -> AgentMessage | None:
        return parse_jsonl_history_entry(entry)

    def requires_pane_title_for_detection(
        self,
        pane_current_command: str,  # noqa: ARG002
    ) -> bool:
        return False

    def detect_from_pane_title(
        self,
        pane_current_command: str,  # noqa: ARG002
        pane_title: str,  # noqa: ARG002
    ) -> bool:
        return False

    def discover_transcript(
        self,
        cwd: str,  # noqa: ARG002
        window_key: str,  # noqa: ARG002
        *,
        max_age: float | None = None,  # noqa: ARG002
        exclude_session_ids: set[str] | None = None,  # noqa: ARG002
        exclude_transcript_paths: set[str] | None = None,  # noqa: ARG002
    ) -> SessionStartEvent | None:
        return None

    def discover_commands(
        self,
        base_dir: str,  # noqa: ARG002
    ) -> list[DiscoveredCommand]:
        return [
            DiscoveredCommand(name=name, description=desc, source="builtin")
            for name, desc in self._BUILTINS.items()
        ]

    def build_status_snapshot(
        self,
        transcript_path: str,  # noqa: ARG002
        *,
        display_name: str = "",  # noqa: ARG002
        session_id: str = "",  # noqa: ARG002
        cwd: str = "",  # noqa: ARG002
    ) -> str | None:
        return None

    def has_output_since(
        self,
        transcript_path: str,  # noqa: ARG002
        offset: int,  # noqa: ARG002
    ) -> bool:
        return False

    async def scrape_current_mode(
        self,
        window_id: str,  # noqa: ARG002
    ) -> str | None:
        return None

    async def seed_task_state(
        self,
        window_id: str,  # noqa: ARG002
        session_id: str,  # noqa: ARG002
        transcript_path: str,  # noqa: ARG002
    ) -> None:
        return None

    def apply_task_entries(
        self,
        window_id: str,  # noqa: ARG002
        session_id: str,  # noqa: ARG002
        entries: list[dict],  # noqa: ARG002
    ) -> None:
        return None
