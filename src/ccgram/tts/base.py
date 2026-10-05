from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TtsSynthesisError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TtsAudio:
    data: bytes
    filename: str = "reply.mp3"


class SpeechSynthesizer(Protocol):
    async def synthesize(self, text: str) -> TtsAudio: ...
