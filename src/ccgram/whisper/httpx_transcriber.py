import httpx

from .base import TranscriptionResult

_OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatTranscriber:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        language: str | None = None,
    ) -> None:
        self.model = model
        self.language = language
        self._api_key = api_key
        self._base_url = (base_url or _OPENAI_BASE_URL).rstrip("/")

    async def transcribe(
        self, audio_bytes: bytes, filename: str
    ) -> TranscriptionResult:
        if len(audio_bytes) > 25 * 1024 * 1024:
            msg = "Audio file too large (max 25 MB)"
            raise ValueError(msg)

        data = {"model": self.model}
        if self.language:
            data["language"] = self.language

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self._base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    files={"file": (filename, audio_bytes)},
                    data=data,
                    timeout=60.0,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                msg = f"Transcription failed: {exc.response.status_code} {exc.response.text}"
                raise RuntimeError(msg) from exc
            except httpx.HTTPError as exc:
                msg = f"Transcription failed: {exc}"
                raise RuntimeError(msg) from exc

        try:
            text = response.json()["text"]
        except (KeyError, ValueError) as exc:
            msg = f"Unexpected API response: {response.text[:200]}"
            raise RuntimeError(msg) from exc

        return TranscriptionResult(text=text, language=None)
