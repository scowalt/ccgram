from ..expandable_quote import EXPANDABLE_QUOTE_END, EXPANDABLE_QUOTE_START
from ..telegram_sender import split_message

_MAX_USER_MSG_LENGTH = 3000


def build_response_parts(
    text: str,
    is_complete: bool,
    content_type: str = "text",
    role: str = "assistant",
) -> list[str]:
    text = text.strip()

    if role == "user":
        prefix = "\U0001f464 "
        if len(text) > _MAX_USER_MSG_LENGTH:
            text = text[:_MAX_USER_MSG_LENGTH] + "\u2026"
        return [f"{prefix}{text}"]

    if content_type == "thinking" and is_complete:
        start_tag = EXPANDABLE_QUOTE_START
        end_tag = EXPANDABLE_QUOTE_END
        max_thinking = 500
        if start_tag in text and end_tag in text:
            inner = text[text.index(start_tag) + len(start_tag) : text.index(end_tag)]
            if len(inner) > max_thinking:
                inner = inner[:max_thinking] + "\n\n\u2026 (thinking truncated)"
            text = start_tag + inner + end_tag
        elif len(text) > max_thinking:
            text = text[:max_thinking] + "\n\n\u2026 (thinking truncated)"

    if content_type == "thinking":
        prefix = "\U0001f9e0 Thinking\u2026"
        separator = "\n"
    else:
        prefix = ""
        separator = ""

    if EXPANDABLE_QUOTE_START in text:
        if prefix:
            return [f"{prefix}{separator}{text}"]
        else:
            return [text]

    max_text = 3000 - len(prefix) - len(separator)

    text_chunks = split_message(text, max_length=max_text)
    total = len(text_chunks)

    if total == 1:
        if prefix:
            return [f"{prefix}{separator}{text_chunks[0]}"]
        else:
            return [text_chunks[0]]

    parts = []
    for i, chunk in enumerate(text_chunks, 1):
        if prefix:
            parts.append(f"{prefix}{separator}{chunk}\n\n[{i}/{total}]")
        else:
            parts.append(f"{chunk}\n\n[{i}/{total}]")
    return parts
