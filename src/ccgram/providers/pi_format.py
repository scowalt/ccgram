from __future__ import annotations

import json
from typing import Any

from ccgram.expandable_quote import format_expandable_quote
from ccgram.providers.base import AgentMessage
from ccgram.tool_format import format_tool_line

_TOOL_NAME_ALIASES: dict[str, str] = {
    "bash": "Bash",
    "read": "Read",
    "edit": "Edit",
    "write": "Write",
    "grep": "Grep",
    "glob": "Glob",
    "find": "Find",
    "ls": "List",
    "list": "List",
    "webfetch": "WebFetch",
    "web_fetch": "WebFetch",
    "websearch": "WebSearch",
    "web_search": "WebSearch",
}

_TOOL_RESULT_QUOTE_THRESHOLD = 3
_PENDING_TUPLE_LEN = 2

Pending = dict[str, tuple[str, str]]


def canonical_tool_name(name: str) -> str:
    return _TOOL_NAME_ALIASES.get(name.lower(), name)


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def format_tool_result_text(raw_name: str, output: str) -> str:
    if not output:
        return "Done"
    line_count = output.count("\n") + 1
    needs_quote = raw_name == "bash" or line_count > _TOOL_RESULT_QUOTE_THRESHOLD
    if needs_quote:
        unit = "line" if line_count == 1 else "lines"
        stats = f"  \u23bf  {line_count} {unit}"
        return stats + "\n" + format_expandable_quote(output)
    return output


_TOOL_SUMMARY_ARG_KEYS: dict[str, tuple[str, ...]] = {
    "bash": ("command",),
    "read": ("path", "file_path"),
    "edit": ("path", "file_path"),
    "write": ("path", "file_path"),
    "grep": ("pattern",),
    "glob": ("pattern",),
}


def _first_string_arg(args: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _tool_call_summary(raw_name: str, args: dict[str, Any]) -> str:
    display = canonical_tool_name(raw_name)

    preferred = _first_string_arg(args, _TOOL_SUMMARY_ARG_KEYS.get(raw_name, ()))
    if preferred:
        return format_tool_line(display, preferred)

    for value in args.values():
        if isinstance(value, str) and value:
            return format_tool_line(display, value)
    return format_tool_line(display, "")


def parse_session_header(entry: dict[str, Any]) -> dict[str, str] | None:
    if entry.get("type") != "session":
        return None
    session_id = entry.get("id", "")
    cwd = entry.get("cwd", "")
    if not (isinstance(session_id, str) and session_id):
        return None
    if not (isinstance(cwd, str) and cwd):
        return None
    return {"id": session_id, "cwd": cwd}


def read_session_header(file_path: str) -> dict[str, str] | None:
    try:
        with open(file_path, encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return None
    if not first:
        return None
    try:
        data = json.loads(first)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return parse_session_header(data)


def _tool_call_block_to_message(
    block: dict[str, Any], pending: Pending, timestamp: str | None = None
) -> AgentMessage:
    call_id_raw = block.get("id", "")
    call_id = call_id_raw if isinstance(call_id_raw, str) else ""
    raw_name_raw = block.get("name", "unknown")
    raw_name = raw_name_raw if isinstance(raw_name_raw, str) else "unknown"
    display = canonical_tool_name(raw_name)
    args = block.get("arguments", {})
    if not isinstance(args, dict):
        args = {}
    if call_id:
        pending[call_id] = (raw_name, display)
    return AgentMessage(
        text=_tool_call_summary(raw_name, args),
        role="assistant",
        content_type="tool_use",
        tool_use_id=call_id or None,
        tool_name=display,
        timestamp=timestamp,
    )


def _assistant_error_message(
    msg: dict[str, Any], timestamp: str | None = None
) -> AgentMessage | None:
    if msg.get("stopReason") != "error":
        return None
    err = msg.get("errorMessage")
    if not isinstance(err, str) or not err:
        return None
    return AgentMessage(
        text=f"\u26a0 API error: {err}",
        role="assistant",
        content_type="text",
        timestamp=timestamp,
    )


def _parse_assistant_content(
    content: Any,
    pending: Pending,
    timestamp: str | None,
) -> list[AgentMessage]:
    if isinstance(content, str):
        if content.strip():
            return [
                AgentMessage(
                    text=content.strip(),
                    role="assistant",
                    content_type="text",
                    timestamp=timestamp,
                )
            ]
        return []
    if not isinstance(content, list):
        return []
    messages: list[AgentMessage] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                messages.append(
                    AgentMessage(
                        text=text.strip(),
                        role="assistant",
                        content_type="text",
                        timestamp=timestamp,
                    )
                )
        elif btype == "toolCall":
            messages.append(_tool_call_block_to_message(block, pending, timestamp))
    return messages


def parse_assistant(
    msg: dict[str, Any], pending: Pending, timestamp: str | None = None
) -> tuple[list[AgentMessage], Pending]:
    content = msg.get("content", [])
    messages = _parse_assistant_content(content, pending, timestamp)
    err_msg = _assistant_error_message(msg, timestamp)
    if err_msg is not None:
        messages.append(err_msg)

    return messages, pending


def parse_tool_result(
    msg: dict[str, Any], pending: Pending, timestamp: str | None = None
) -> tuple[list[AgentMessage], Pending]:
    call_id_value = msg.get("toolCallId", "")
    call_id = call_id_value if isinstance(call_id_value, str) else ""

    raw_name = "unknown"
    display = "unknown"
    if call_id and call_id in pending:
        raw_name, display = pending.pop(call_id)
    else:
        native = msg.get("toolName")
        if isinstance(native, str) and native:
            raw_name = native
            display = canonical_tool_name(native)

    output = extract_text(msg.get("content", ""))
    is_error = bool(msg.get("isError"))

    if is_error and output:
        text = f"Error: {output}"
    elif is_error:
        text = "Error"
    else:
        text = format_tool_result_text(raw_name, output)

    return (
        [
            AgentMessage(
                text=text,
                role="assistant",
                content_type="tool_result",
                tool_use_id=call_id or None,
                tool_name=display,
                timestamp=timestamp,
            )
        ],
        pending,
    )


def parse_bash_execution(
    msg: dict[str, Any], timestamp: str | None = None
) -> list[AgentMessage]:
    if msg.get("excludeFromContext"):
        return []

    command = msg.get("command") if isinstance(msg.get("command"), str) else ""
    output = msg.get("output") if isinstance(msg.get("output"), str) else ""
    exit_code = msg.get("exitCode")
    cancelled = bool(msg.get("cancelled"))

    lines: list[str] = []
    if command:
        lines.append(f"$ {command}")
    if output:
        line_count = output.count("\n") + 1
        if line_count > _TOOL_RESULT_QUOTE_THRESHOLD:
            unit = "line" if line_count == 1 else "lines"
            lines.append(f"  \u23bf  {line_count} {unit}")
            lines.append(format_expandable_quote(output))
        else:
            lines.append(output)
    if cancelled:
        lines.append("(cancelled)")
    elif isinstance(exit_code, int) and exit_code != 0:
        lines.append(f"exit code {exit_code}")

    if not lines:
        return []

    return [
        AgentMessage(
            text="\n".join(lines),
            role="assistant",
            content_type="tool_result",
            tool_name="Bash",
            timestamp=timestamp,
        )
    ]


def parse_user(msg: dict[str, Any], timestamp: str | None = None) -> list[AgentMessage]:
    text = extract_text(msg.get("content", "")).strip()
    if not text:
        return []
    return [
        AgentMessage(text=text, role="user", content_type="text", timestamp=timestamp)
    ]


def normalize_pending(value: Any) -> Pending:
    out: Pending = {}
    if not isinstance(value, dict):
        return out
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(item, tuple) and len(item) == _PENDING_TUPLE_LEN:
            raw, display = item
            if isinstance(raw, str) and isinstance(display, str):
                out[key] = (raw, display)
        elif isinstance(item, str):
            out[key] = (item, canonical_tool_name(item))
    return out
