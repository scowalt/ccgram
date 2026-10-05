import re

from telegram import MessageEntity as TelegramEntity

from telegramify_markdown import config as _tm_config
from telegramify_markdown import convert as _tm_convert
from telegramify_markdown import utf16_len as _utf16_len
from telegramify_markdown.entity import MessageEntity as _LibEntity

from .expandable_quote import EXPANDABLE_QUOTE_END, EXPANDABLE_QUOTE_START

_tm_config.get_runtime_config().cite_expandable = False

_EXPQUOTE_RE = re.compile(
    re.escape(EXPANDABLE_QUOTE_START) + r"([\s\S]*?)" + re.escape(EXPANDABLE_QUOTE_END)
)

_EXPQUOTE_MAX_RENDERED = 3800

_MIN_PARTIAL_LINE_LEN = 20

_FENCE_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
_INDENTED_CODE_RE = re.compile(r"(?<=\n\n)((?:    .+\n?)+)")
_INDENTED_LINE_RE = re.compile(r"^    ", re.MULTILINE)


def _strip_indented_code_blocks(text: str) -> str:
    parts: list[str] = []
    inside_fence = False
    fence_marker = ""
    last_end = 0

    for m in _FENCE_RE.finditer(text):
        marker = m.group(1)
        if not inside_fence:
            parts.append(_deindent(text[last_end : m.start()], last_end == 0))
            inside_fence = True
            fence_marker = marker
            last_end = m.start()
        elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
            end = m.end()
            parts.append(text[last_end:end])
            last_end = end
            inside_fence = False
            fence_marker = ""

    tail = text[last_end:]
    if inside_fence:
        parts.append(tail)
    else:
        parts.append(_deindent(tail, last_end == 0))

    return "".join(parts)


def _deindent(text: str, is_start: bool) -> str:
    if is_start:
        text = re.sub(
            r"^((?:    .+\n?)+)",
            lambda m: _INDENTED_LINE_RE.sub("", m.group(0)),
            text,
        )
    return _INDENTED_CODE_RE.sub(
        lambda m: _INDENTED_LINE_RE.sub("", m.group(0)),
        text,
    )


def _lib_entity_to_telegram(ent: _LibEntity, offset_shift: int = 0) -> TelegramEntity:
    return TelegramEntity(
        type=ent.type,
        offset=ent.offset + offset_shift,
        length=ent.length,
        url=ent.url,
        language=ent.language,
        custom_emoji_id=ent.custom_emoji_id,
    )


def _convert_segment(text: str) -> tuple[str, list[TelegramEntity]]:
    preprocessed = _strip_indented_code_blocks(text)
    plain, lib_entities = _tm_convert(preprocessed)
    tg_entities = [_lib_entity_to_telegram(e) for e in lib_entities]
    return plain, tg_entities


def _truncate_quote_text(text: str) -> tuple[str, bool]:
    if _utf16_len(text) <= _EXPQUOTE_MAX_RENDERED:
        return text, False

    lines = text.split("\n")
    built: list[str] = []
    total_len = 0
    suffix = "\n… (truncated)"
    budget = _EXPQUOTE_MAX_RENDERED - _utf16_len(suffix)

    for line in lines:
        line_cost = _utf16_len(line) + 1
        if total_len + line_cost > budget:
            remaining = budget - total_len - 1
            if remaining > _MIN_PARTIAL_LINE_LEN:
                built.append(line[:remaining])
            built.append("… (truncated)")
            return "\n".join(built), True
        built.append(line)
        total_len += line_cost

    return "\n".join(built), True


def convert_to_entities(text: str) -> tuple[str, list[TelegramEntity]]:
    segments: list[tuple[bool, str]] = []
    last_end = 0
    for m in _EXPQUOTE_RE.finditer(text):
        if m.start() > last_end:
            segments.append((False, text[last_end : m.start()]))
        segments.append((True, m.group(1)))
        last_end = m.end()
    if last_end < len(text):
        segments.append((False, text[last_end:]))

    if not segments:
        return _convert_segment(text)

    result_text = ""
    result_entities: list[TelegramEntity] = []

    for is_quote, segment in segments:
        if is_quote:
            quote_text, _was_truncated = _truncate_quote_text(segment)
            offset = _utf16_len(result_text)
            length = _utf16_len(quote_text)
            result_entities.append(
                TelegramEntity(
                    type=TelegramEntity.EXPANDABLE_BLOCKQUOTE,
                    offset=offset,
                    length=length,
                )
            )
            result_text += quote_text
        else:
            plain, entities = _convert_segment(segment)
            offset_shift = _utf16_len(result_text)
            for ent in entities:
                result_entities.append(
                    TelegramEntity(
                        type=ent.type,
                        offset=ent.offset + offset_shift,
                        length=ent.length,
                        url=ent.url,
                        language=ent.language,
                        custom_emoji_id=ent.custom_emoji_id,
                    )
                )
            result_text += plain

    return result_text, result_entities
