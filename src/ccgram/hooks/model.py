from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
ProviderName: TypeAlias = Literal["claude", "pi", "codex", "gemini"]


@dataclass(frozen=True, slots=True)
class NormalizedHookEvent:
    provider_name: ProviderName
    native_event_name: str
    canonical_event_name: str
    session_id: str
    cwd: Path | None
    transcript_path: Path | None
    data: dict[str, JsonValue]


class HookAdapter(Protocol):
    provider_name: ProviderName
    event_types: tuple[str, ...]
    installable_events: tuple[str, ...]

    def normalize(self, payload: dict[str, object]) -> NormalizedHookEvent | None: ...
