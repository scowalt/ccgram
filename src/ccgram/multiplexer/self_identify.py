from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

TmuxQuery = Callable[[str], "tuple[str, str, str, str] | None"]

HerdrQuery = Callable[[str], "str | None"]


@dataclass(frozen=True)
class SelfIdentity:
    mux: str
    session_window_key: str
    window_id: str
    window_name: str
    pane_tty: str = ""


def resolve_self_identity(
    env: Mapping[str, str],
    *,
    tmux_query: TmuxQuery,
    herdr_query: HerdrQuery | None = None,
) -> SelfIdentity | None:
    herdr_pane = env.get("HERDR_PANE_ID", "")
    tmux_pane = env.get("TMUX_PANE", "")
    prefer_herdr = env.get("CCGRAM_MULTIPLEXER", "tmux").strip().lower() == "herdr"

    if herdr_pane and prefer_herdr:
        tab_id = herdr_query(herdr_pane) if herdr_query is not None else None
        if tab_id is None:
            return None
        return SelfIdentity(
            mux="herdr",
            session_window_key=f"herdr:{tab_id}",
            window_id=tab_id,
            window_name="",
        )

    if tmux_pane:
        resolved = tmux_query(tmux_pane)
        if resolved is None:
            return None
        session_window_key, window_id, window_name, pane_tty = resolved
        return SelfIdentity(
            mux="tmux",
            session_window_key=session_window_key,
            window_id=window_id,
            window_name=window_name,
            pane_tty=pane_tty,
        )

    if herdr_pane:
        tab_id = herdr_query(herdr_pane) if herdr_query is not None else None
        if tab_id is None:
            return None
        return SelfIdentity(
            mux="herdr",
            session_window_key=f"herdr:{tab_id}",
            window_id=tab_id,
            window_name="",
        )

    return None
