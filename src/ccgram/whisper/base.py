from dataclasses import dataclass
from typing import Protocol


@dataclass
class TranscriptionResult:
    text: str
    language: str | None = None


class WhisperTranscriber(Protocol):
    async def transcribe(
        self, audio_bytes: bytes, filename: str
    ) -> TranscriptionResult: ...
