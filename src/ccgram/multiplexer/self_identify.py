"""Backend-neutral hook identity resolver.

The Claude Code hook runs as a separate process spawned inside a multiplexer
pane; it cannot import bot config or wire the ``multiplexer`` proxy. It only
needs to answer "which window am I?" from the environment. Each backend exposes
that differently — tmux via ``$TMUX_PANE`` + ``tmux display-message``, herdr via
``$HERDR_PANE_ID`` — so this module uses ``CCGRAM_MULTIPLEXER`` to break ties
when both variables are present (a pane can inherit an outer multiplexer
environment) and returns a neutral ``SelfIdentity``.

The tmux probe (a ``display-message`` subprocess) is injected as ``tmux_query``
so this module stays I/O-free and table-testable; the hook supplies its own
``_resolve_window_id`` as the default probe. The herdr branch resolves the pane
id to a tab id via an optional injected ``herdr_query`` (hook.py supplies its
own ``_resolve_herdr_tab_id`` probe); when absent or failing it returns None so
hooks do not write phantom pane-keyed session_map entries.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

# tmux_query returns ``(session_window_key, window_id, window_name, pane_tty)``
# or None on failure — the exact shape of ``hook._resolve_window_id``.
TmuxQuery = Callable[[str], "tuple[str, str, str, str] | None"]

# herdr_query returns the ``tab_id`` string for a given pane id, or None on
# failure (herdr not available, socket down, …). When None the herdr branch
# returns None from ``resolve_self_identity`` (symmetric with the tmux branch),
# so the hook skips the session_map write rather than binding a phantom key.
HerdrQuery = Callable[[str], "str | None"]


@dataclass(frozen=True)
class SelfIdentity:
    """Neutral identity of the window that fired the hook.

    ``session_window_key`` is the ``session_map.json`` key (``<session>:<id>``
    for tmux, ``herdr:<tab_id>`` for herdr). ``pane_tty`` is tmux-only (herdr
    does not expose a tty).
    """

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
    """Resolve the firing window's identity from ``env``.

    Dispatches on which backend's ``self_identify_env`` var is present:
    ``$HERDR_PANE_ID`` → herdr, ``$TMUX_PANE`` → tmux (via ``tmux_query``).
    Returns None when neither is set or the selected backend probe fails.
    ``CCGRAM_MULTIPLEXER=herdr`` makes Herdr win when both vars are present;
    otherwise tmux wins, matching the default backend.

    For herdr: ``herdr_query(pane_id)`` resolves the pane to its containing tab
    id so ``session_window_key`` becomes ``herdr:<tab_id>`` (matching
    ``list_windows``). Returns None when the probe is None or returns None
    (herdr not installed, socket down) — symmetric with the tmux branch;
    the hook skips the session_map write until the socket is available.
    """
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
