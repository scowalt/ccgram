from __future__ import annotations

import os
import re
from typing import Callable, Iterable

from ccgram.config import config
from .base import SpeechSynthesizer, TtsAudio, TtsSynthesisError

_PAGINATION_RE = re.compile(r"\n\n\[\d+/\d+\]$")
_USER_PREFIX = "\U0001f464 "


def _make_edge() -> SpeechSynthesizer:
    from .edge import EdgeTtsSynthesizer

    return EdgeTtsSynthesizer(voice=config.tts_voice)


def _make_openai() -> SpeechSynthesizer:
    api_key = config.tts_api_key or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        msg = "No API key for OpenAI TTS: set CCGRAM_TTS_API_KEY or OPENAI_API_KEY"
        raise ValueError(msg)
    from .openai import OpenAITtsSynthesizer

    return OpenAITtsSynthesizer(
        api_key=api_key,
        model=config.tts_model,
        voice=config.tts_voice,
    )


_PROVIDERS: dict[str, Callable[[], SpeechSynthesizer]] = {
    "edge": _make_edge,
    "openai": _make_openai,
}


def prepare_tts_text(parts: Iterable[str]) -> str:
    from ccgram.entity_formatting import convert_to_entities

    cleaned_parts: list[str] = []
    for part in parts:
        cleaned = _PAGINATION_RE.sub("", part).strip()
        if cleaned:
            cleaned_parts.append(cleaned)
    combined = "\n".join(cleaned_parts)
    if combined.startswith(_USER_PREFIX):
        combined = combined[len(_USER_PREFIX) :]
    plain_text, _entities = convert_to_entities(combined)
    return plain_text.strip()


def get_synthesizer() -> SpeechSynthesizer | None:
    provider = config.tts_provider
    if not provider:
        return None

    factory = _PROVIDERS.get(provider)
    if factory is None:
        msg = f"Unknown TTS provider: {provider!r}. Supported: {list(_PROVIDERS)}"
        raise ValueError(msg)

    return factory()


__all__ = [
    "SpeechSynthesizer",
    "TtsAudio",
    "TtsSynthesisError",
    "get_synthesizer",
    "prepare_tts_text",
]
