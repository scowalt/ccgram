import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ccgram.expandable_quote import EXPANDABLE_QUOTE_START, format_expandable_quote
from ccgram.tool_format import format_tool_line

from .utils import shorten_path


@dataclass
class ParsedMessage:
    message_type: str
    text: str
    tool_name: str | None = None


@dataclass
class ParsedEntry:
    role: str
    text: str
    content_type: str
    tool_use_id: str | None = None
    timestamp: str | None = None
    tool_name: str | None = None


@dataclass
class PendingToolInfo:
    summary: str
    tool_name: str
    input_data: Any = None


@dataclass
class _ParseState:
    pending_tools: dict[str, PendingToolInfo] = field(default_factory=dict)
    last_cmd_name: str | None = None


class TranscriptParser:
    _NO_CONTENT_PLACEHOLDER = "(no content)"
    _INTERRUPTED_TEXT = "[Request interrupted by user for tool use]"
    _ERROR_SUMMARY_LIMIT = 100

    @staticmethod
    def parse_line(line: str) -> dict | None:
        line = line.strip()
        if not line:
            return None

        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def get_message_type(data: dict) -> str | None:
        return data.get("type")

    @staticmethod
    def is_user_message(data: dict) -> bool:
        return data.get("type") == "user"

    @staticmethod
    def extract_text_only(content_list: list[Any]) -> str:
        if not isinstance(content_list, list):
            if isinstance(content_list, str):
                return content_list
            return ""

        texts = []
        for item in content_list:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if text:
                    texts.append(TranscriptParser._RE_ANSI.sub("", text))

        return "\n".join(texts)

    _RE_ANSI = re.compile(r"\x1b\[[0-9;]*m")

    _RE_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>")
    _RE_LOCAL_STDOUT = re.compile(
        r"<local-command-stdout>(.*?)</local-command-stdout>", re.DOTALL
    )
    _RE_SYSTEM_TAGS = re.compile(
        r"<(bash-input|bash-stdout|bash-stderr|local-command-caveat|system-reminder)"
    )

    @staticmethod
    def _format_edit_diff(old_string: str, new_string: str) -> str:
        old_lines = old_string.splitlines(keepends=True)
        new_lines = new_string.splitlines(keepends=True)
        diff = difflib.unified_diff(old_lines, new_lines, lineterm="")
        result_lines: list[str] = []
        for line in diff:
            if line.startswith("---") or line.startswith("+++"):
                continue
            result_lines.append(line.rstrip("\n"))
        return "\n".join(result_lines)

    @classmethod
    def format_tool_use_summary(
        cls, name: str, input_data: dict | Any, cwd: str | None = None
    ) -> str:
        if not isinstance(input_data, dict):
            return format_tool_line(name, "")

        summary = ""
        if name in ("Read", "Glob"):
            summary = input_data.get("file_path") or input_data.get("pattern", "")
            if name == "Read":
                summary = shorten_path(summary, cwd)
        elif name == "Write":
            summary = shorten_path(input_data.get("file_path", ""), cwd)
        elif name in ("Edit", "NotebookEdit"):
            summary = input_data.get("file_path") or input_data.get("notebook_path", "")
            summary = shorten_path(summary, cwd)
        elif name == "Bash":
            summary = input_data.get("command", "")
        elif name == "Grep":
            summary = input_data.get("pattern", "")
        elif name == "Task":
            summary = input_data.get("description", "")
        elif name == "TaskCreate":
            summary = cls._summarize_task_create(input_data)
        elif name == "TaskUpdate":
            summary = cls._summarize_task_update(input_data)
        elif name == "TaskList":
            summary = cls._summarize_task_list(input_data)
        elif name == "WebFetch":
            summary = input_data.get("url", "")
        elif name == "WebSearch":
            summary = input_data.get("query", "")
        elif name == "TodoWrite":
            todos = input_data.get("todos", [])
            if isinstance(todos, list):
                summary = f"{len(todos)} item(s)"
        elif name == "TodoRead":
            summary = ""
        elif name == "AskUserQuestion":
            questions = input_data.get("questions", [])
            if isinstance(questions, list) and questions:
                q = questions[0]
                if isinstance(q, dict):
                    summary = q.get("question", "")
        elif name == "ExitPlanMode":
            summary = ""
        elif name == "Skill":
            summary = input_data.get("skill", "")
        else:
            for v in input_data.values():
                if isinstance(v, str) and v:
                    summary = v
                    break

        return format_tool_line(name, summary)

    @staticmethod
    def _summarize_task_create(input_data: dict[str, Any]) -> str:
        return (
            input_data.get("subject")
            or input_data.get("activeForm")
            or input_data.get("description")
            or ""
        )

    @staticmethod
    def _summarize_task_update(input_data: dict[str, Any]) -> str:
        label = (
            input_data.get("subject")
            or input_data.get("activeForm")
            or input_data.get("description")
            or input_data.get("taskId")
            or ""
        )
        status = input_data.get("status")
        if isinstance(status, str) and status:
            status_label = status.replace("_", " ")
            if label:
                return f"{label} -> {status_label}"
            return status_label
        return label

    @staticmethod
    def _summarize_task_list(input_data: dict[str, Any]) -> str:
        tasks = input_data.get("tasks")
        if isinstance(tasks, list):
            return f"{len(tasks)} task(s)"
        return "refresh"

    @staticmethod
    def extract_tool_result_text(content: list | Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    t = item.get("text", "")
                    if t:
                        parts.append(t)
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return ""

    @classmethod
    def parse_message(cls, data: dict) -> ParsedMessage | None:
        msg_type = cls.get_message_type(data)

        if msg_type not in ("user", "assistant"):
            return None

        message = data.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content", "")

        if isinstance(content, list):
            text = cls.extract_text_only(content)
        else:
            text = str(content) if content else ""

        if msg_type == "user" and text:
            stdout_match = cls._RE_LOCAL_STDOUT.search(text)
            if stdout_match:
                stdout = stdout_match.group(1).strip()
                cmd_match = cls._RE_COMMAND_NAME.search(text)
                cmd = cmd_match.group(1) if cmd_match else None
                return ParsedMessage(
                    message_type="local_command",
                    text=stdout,
                    tool_name=cmd,
                )
            cmd_match = cls._RE_COMMAND_NAME.search(text)
            if cmd_match:
                return ParsedMessage(
                    message_type="local_command_invoke",
                    text="",
                    tool_name=cmd_match.group(1),
                )

        return ParsedMessage(
            message_type=msg_type,
            text=text,
        )

    @staticmethod
    def get_timestamp(data: dict) -> str | None:
        return data.get("timestamp")

    @classmethod
    def _format_tool_result_text(cls, text: str, tool_name: str | None = None) -> str:
        if not text:
            return ""

        line_count = text.count("\n") + 1 if text else 0

        if tool_name == "Read":
            return f"  ⎿  {line_count} lines"

        elif tool_name == "Write":
            return f"  ⎿  {line_count} lines written"

        elif tool_name == "Bash":
            if line_count > 0:
                stats = f"  ⎿  {line_count} lines"
                return stats + "\n" + format_expandable_quote(text)
            return format_expandable_quote(text)

        elif tool_name == "Grep":
            matches = sum(1 for line in text.split("\n") if line.strip())
            stats = f"  ⎿  {matches} matches"
            return stats + "\n" + format_expandable_quote(text)

        elif tool_name == "Glob":
            files = sum(1 for line in text.split("\n") if line.strip())
            stats = f"  ⎿  {files} files"
            return stats + "\n" + format_expandable_quote(text)

        elif tool_name == "Task":
            if line_count > 0:
                stats = f"  ⎿  {line_count} lines"
                return stats + "\n" + format_expandable_quote(text)
            return format_expandable_quote(text)

        elif tool_name == "WebFetch":
            char_count = len(text)
            stats = f"  ⎿  {char_count} chars"
            return stats + "\n" + format_expandable_quote(text)

        elif tool_name == "WebSearch":
            results = text.count("\n\n") + 1 if text else 0
            stats = f"  ⎿  {results} results"
            return stats + "\n" + format_expandable_quote(text)

        return format_expandable_quote(text)

    @classmethod
    def _handle_tool_use_block(
        cls,
        block: dict,
        state: "_ParseState",
        timestamp: str | None,
        cwd: str | None,
    ) -> list[ParsedEntry]:
        name = block.get("name", "unknown")
        inp = block.get("input", {})
        tool_id = block.get("id", "")
        summary = cls.format_tool_use_summary(name, inp, cwd=cwd)
        entries: list[ParsedEntry] = []

        if name == "ExitPlanMode" and isinstance(inp, dict):
            plan = inp.get("plan", "")
            if plan:
                entries.append(
                    ParsedEntry(
                        role="assistant",
                        text=plan,
                        content_type="text",
                        timestamp=timestamp,
                    )
                )

        if tool_id:
            input_data = inp if name in ("Edit", "NotebookEdit") else None
            state.pending_tools[tool_id] = PendingToolInfo(
                summary=summary,
                tool_name=name,
                input_data=input_data,
            )

        entries.append(
            ParsedEntry(
                role="assistant",
                text=summary,
                content_type="tool_use",
                tool_use_id=tool_id or None,
                timestamp=timestamp,
                tool_name=name,
            )
        )
        return entries

    @classmethod
    def _handle_tool_result_block(
        cls,
        block: dict,
        state: "_ParseState",
        timestamp: str | None,
    ) -> "ParsedEntry | None":
        tool_use_id = block.get("tool_use_id", "")
        result_content = block.get("content", "")
        result_text = cls.extract_tool_result_text(result_content)
        is_error = block.get("is_error", False)
        is_interrupted = result_text == cls._INTERRUPTED_TEXT
        tool_info = state.pending_tools.pop(tool_use_id, None)
        _tuid = tool_use_id or None

        if tool_info is None:
            tool_summary = None
            tool_name = None
            tool_input_data = None
        else:
            tool_summary = tool_info.summary
            tool_name = tool_info.tool_name
            tool_input_data = tool_info.input_data

        if is_interrupted:
            entry_text = tool_summary or ""
            entry_text = (
                (entry_text + "\n⏹ Interrupted") if entry_text else "⏹ Interrupted"
            )
            return ParsedEntry(
                role="assistant",
                text=entry_text,
                content_type="tool_result",
                tool_use_id=_tuid,
                timestamp=timestamp,
            )

        if is_error:
            entry_text = tool_summary or "**Error**"
            if result_text:
                error_summary = result_text.split("\n")[0]
                if len(error_summary) > cls._ERROR_SUMMARY_LIMIT:
                    error_summary = error_summary[: cls._ERROR_SUMMARY_LIMIT] + "…"
                entry_text += f"\n  ⎿  ⚠️ {error_summary}"
                if "\n" in result_text:
                    entry_text += "\n" + format_expandable_quote(result_text)
            else:
                entry_text += "\n  ⎿  ⚠️ Error"
            return ParsedEntry(
                role="assistant",
                text=entry_text,
                content_type="tool_result",
                tool_use_id=_tuid,
                timestamp=timestamp,
            )

        if tool_summary:
            entry_text = tool_summary
            if tool_name == "Edit" and tool_input_data and result_text:
                old_s = tool_input_data.get("old_string", "")
                new_s = tool_input_data.get("new_string", "")
                if old_s and new_s:
                    diff_text = cls._format_edit_diff(old_s, new_s)
                    if diff_text:
                        added = sum(
                            1
                            for line in diff_text.split("\n")
                            if line.startswith("+") and not line.startswith("+++")
                        )
                        removed = sum(
                            1
                            for line in diff_text.split("\n")
                            if line.startswith("-") and not line.startswith("---")
                        )
                        stats = f"  ⎿  +{added} −{removed}"
                        entry_text += (
                            "\n" + stats + "\n" + format_expandable_quote(diff_text)
                        )
            elif result_text and EXPANDABLE_QUOTE_START not in tool_summary:
                entry_text += "\n" + cls._format_tool_result_text(
                    result_text, tool_name
                )
            return ParsedEntry(
                role="assistant",
                text=entry_text,
                content_type="tool_result",
                tool_use_id=_tuid,
                timestamp=timestamp,
            )

        if result_text:
            return ParsedEntry(
                role="assistant",
                text=cls._format_tool_result_text(result_text, tool_name),
                content_type="tool_result",
                tool_use_id=_tuid,
                timestamp=timestamp,
            )

        return None

    @classmethod
    def _handle_local_command_entry(
        cls,
        parsed: ParsedMessage,
        state: "_ParseState",
        timestamp: str | None,
    ) -> "ParsedEntry | None":
        if parsed.message_type == "local_command_invoke":
            state.last_cmd_name = parsed.tool_name
            return None

        cmd = parsed.tool_name or state.last_cmd_name or ""
        text = parsed.text
        if cmd:
            formatted = (
                f"❯ `{cmd}`\n```\n{text}\n```"
                if "\n" in text
                else f"❯ `{cmd}`\n`{text}`"
            )
        else:
            formatted = f"```\n{text}\n```" if "\n" in text else f"`{text}`"
        state.last_cmd_name = None
        return ParsedEntry(
            role="assistant",
            text=formatted,
            content_type="local_command",
            timestamp=timestamp,
        )

    @classmethod
    def _handle_assistant_entry(
        cls,
        content: list,
        state: "_ParseState",
        timestamp: str | None,
        cwd: str | None,
    ) -> list[ParsedEntry]:
        entries: list[ParsedEntry] = []
        has_text = False

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")

            if btype == "text":
                t = cls._RE_ANSI.sub("", block.get("text", "")).strip()
                if t and t != cls._NO_CONTENT_PLACEHOLDER:
                    entries.append(
                        ParsedEntry(
                            role="assistant",
                            text=t,
                            content_type="text",
                            timestamp=timestamp,
                        )
                    )
                    has_text = True

            elif btype == "tool_use":
                entries.extend(cls._handle_tool_use_block(block, state, timestamp, cwd))

            elif btype == "thinking":
                thinking_text = block.get("thinking", "")
                if thinking_text:
                    entries.append(
                        ParsedEntry(
                            role="assistant",
                            text=format_expandable_quote(thinking_text),
                            content_type="thinking",
                            timestamp=timestamp,
                        )
                    )
                elif not has_text:
                    entries.append(
                        ParsedEntry(
                            role="assistant",
                            text="(thinking)",
                            content_type="thinking",
                            timestamp=timestamp,
                        )
                    )

        return entries

    @classmethod
    def _handle_user_entry(
        cls,
        content: list,
        state: "_ParseState",
        timestamp: str | None,
    ) -> list[ParsedEntry]:
        entries: list[ParsedEntry] = []
        user_text_parts: list[str] = []

        for block in content:
            if not isinstance(block, dict):
                if isinstance(block, str) and block.strip():
                    user_text_parts.append(block.strip())
                continue
            btype = block.get("type", "")

            if btype == "tool_result":
                entry = cls._handle_tool_result_block(block, state, timestamp)
                if entry is not None:
                    entries.append(entry)

            elif btype == "text":
                t = cls._RE_ANSI.sub("", block.get("text", "")).strip()
                if t and not cls._RE_SYSTEM_TAGS.search(t):
                    user_text_parts.append(t)

        if user_text_parts:
            combined = "\n".join(user_text_parts)
            if not cls._RE_LOCAL_STDOUT.search(
                combined
            ) and not cls._RE_COMMAND_NAME.search(combined):
                entries.append(
                    ParsedEntry(
                        role="user",
                        text=combined,
                        content_type="text",
                        timestamp=timestamp,
                    )
                )

        return entries

    @classmethod
    def parse_entries(
        cls,
        entries: list[dict],
        pending_tools: dict[str, PendingToolInfo] | None = None,
        cwd: str | None = None,
    ) -> tuple[list[ParsedEntry], dict[str, PendingToolInfo]]:
        result: list[ParsedEntry] = []
        _carry_over = pending_tools is not None
        state = _ParseState(
            pending_tools={} if pending_tools is None else dict(pending_tools)
        )

        for data in entries:
            msg_type = cls.get_message_type(data)
            if msg_type not in ("user", "assistant"):
                continue

            entry_timestamp = cls.get_timestamp(data)

            message = data.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content", "")
            if not isinstance(content, list):
                content = [{"type": "text", "text": str(content)}] if content else []

            parsed = cls.parse_message(data)

            if parsed and parsed.message_type in (
                "local_command_invoke",
                "local_command",
            ):
                entry = cls._handle_local_command_entry(parsed, state, entry_timestamp)
                if entry is not None:
                    result.append(entry)
                continue
            state.last_cmd_name = None

            if msg_type == "assistant":
                result.extend(
                    cls._handle_assistant_entry(content, state, entry_timestamp, cwd)
                )
            elif msg_type == "user":
                result.extend(cls._handle_user_entry(content, state, entry_timestamp))

        remaining_pending = dict(state.pending_tools)
        if not _carry_over:
            for tool_id, tool_info in state.pending_tools.items():
                result.append(
                    ParsedEntry(
                        role="assistant",
                        text=tool_info.summary,
                        content_type="tool_use",
                        tool_use_id=tool_id,
                    )
                )

        for entry in result:
            entry.text = entry.text.strip()

        return result, remaining_pending
