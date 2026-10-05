from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM_PROMPT = """\
You are a development assistant summarizing what a coding agent accomplished.
Given the recent activity log, write a single-line summary (max 120 chars).
Be specific: mention file names, test counts, command outcomes.
Examples:
- "Fixed auth bug in login.py, all 23 tests pass"
- "Added 3 API endpoints in src/api/, updated OpenAPI spec"
- "Refactored database module — 2 tests failing (test_connection, test_pool)"
Return ONLY the summary line, no quotes or formatting."""

_MAX_ENTRIES = 30
_MAX_RESULT_CHARS = 200
_MAX_ASSISTANT_CHARS = 500


def _read_tail_lines(path: Path, max_lines: int) -> list[str]:
    try:
        size = path.stat().st_size
        if size == 0:
            return []
        chunk_size = min(size, max_lines * 200)
        with path.open("rb") as f:
            f.seek(max(0, size - chunk_size))
            tail = f.read().decode(errors="replace")
        lines = tail.splitlines()
        if chunk_size < size:
            lines = lines[1:]
        return lines[-max_lines:]
    except OSError:
        return []


def _extract_tool_summary(block: dict) -> str | None:
    name = block.get("name", "")
    input_data = block.get("input", {})
    if not isinstance(input_data, dict):
        return f"{name}"

    if name in ("Read", "Glob"):
        return f"{name} {input_data.get('file_path') or input_data.get('pattern', '')}"
    if name == "Edit":
        return f"Edit {input_data.get('file_path', '')}"
    if name == "Write":
        return f"Write {input_data.get('file_path', '')}"
    if name == "Bash":
        cmd = str(input_data.get("command", ""))[:100]
        return f"Bash: {cmd}"
    if name == "Grep":
        return f"Grep {input_data.get('pattern', '')}"
    if name in ("TaskCreate", "TaskUpdate"):
        return f"{name} {input_data.get('subject', '')}"
    return f"{name}"


def _extract_result_snippet(block: dict) -> str:
    content = block.get("content", "")
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        content = "\n".join(parts)
    if not isinstance(content, str):
        content = str(content)
    return content[:_MAX_RESULT_CHARS]


def _parse_entry(raw_line: str) -> tuple[str, list[dict]] | None:
    raw_line = raw_line.strip()
    if not raw_line:
        return None
    try:
        entry = json.loads(raw_line)
    except json.JSONDecodeError:
        return None

    entry_type = entry.get("type")
    message = entry.get("message", {})
    if not isinstance(message, dict):
        return None
    content_blocks = message.get("content", [])
    if not isinstance(content_blocks, list):
        return None
    return entry_type, [b for b in content_blocks if isinstance(b, dict)]


def _process_assistant_blocks(blocks: list[dict], context_parts: list[str]) -> str:
    last_text = ""
    for block in blocks:
        btype = block.get("type")
        if btype == "tool_use":
            summary = _extract_tool_summary(block)
            if summary:
                context_parts.append(f"\u2192 {summary}")
        elif btype == "text":
            text = block.get("text", "")
            if text:
                last_text = text
    return last_text


def _process_user_blocks(blocks: list[dict], context_parts: list[str]) -> None:
    for block in blocks:
        if block.get("type") == "tool_result":
            snippet = _extract_result_snippet(block)
            if snippet:
                first_line = snippet.split("\n", 1)[0][:120]
                context_parts.append(f"  = {first_line}")


def _build_summary_context(lines: list[str]) -> str:
    context_parts: list[str] = []
    last_assistant_text = ""

    for line in lines:
        parsed = _parse_entry(line)
        if parsed is None:
            continue
        entry_type, blocks = parsed

        if entry_type == "assistant":
            text = _process_assistant_blocks(blocks, context_parts)
            if text:
                last_assistant_text = text
        elif entry_type == "user":
            _process_user_blocks(blocks, context_parts)

    if last_assistant_text:
        trimmed = last_assistant_text[-_MAX_ASSISTANT_CHARS:]
        context_parts.append(f"\nFinal response:\n{trimmed}")

    return "\n".join(context_parts)


async def summarize_completion(transcript_path: str) -> str | None:
    if not transcript_path:
        return None

    path = Path(transcript_path)
    if not path.exists():
        return None

    from . import get_text_completer

    completer = get_text_completer()
    if completer is None:
        return None

    try:
        lines = await asyncio.to_thread(_read_tail_lines, path, _MAX_ENTRIES)
        if not lines:
            return None

        context = _build_summary_context(lines)
        if not context.strip():
            return None

        result = await completer.complete(_SUMMARY_SYSTEM_PROMPT, context)
        return result.strip()[:150] if result else None
    except RuntimeError:
        logger.warning("LLM summary failed", exc_info=True)
        return None
