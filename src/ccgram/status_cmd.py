import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from .utils import ccgram_dir, load_ccgram_env, tmux_session_name
from .window_resolver import session_map_prefix_for

_TMUX_FORMAT_PARTS = 2

_MULTIPLEXER_ENV = "CCGRAM_MULTIPLEXER"
_DEFAULT_MULTIPLEXER = "tmux"
_HERDR_BACKEND = "herdr"


def _active_multiplexer_name() -> str:
    return os.environ.get(_MULTIPLEXER_ENV, _DEFAULT_MULTIPLEXER)


def _list_herdr_windows() -> list[dict[str, str]]:
    from .multiplexer import get_multiplexer

    try:
        windows = asyncio.run(get_multiplexer(_HERDR_BACKEND).list_windows())
    except Exception:  # noqa: BLE001
        return []
    return [{"id": w.window_id, "name": w.window_name} for w in windows]


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError, OSError:
        return {}


def _list_tmux_windows(session_name: str) -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            [
                "tmux",
                "list-windows",
                "-t",
                session_name,
                "-F",
                "#{window_id}\t#{window_name}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        windows = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == _TMUX_FORMAT_PARTS:
                windows.append({"id": parts[0], "name": parts[1]})
        return windows
    except OSError, subprocess.TimeoutExpired:
        return []


def _capability_summary() -> tuple[str, str]:
    from .providers import resolve_capabilities

    caps = resolve_capabilities()
    flags = [
        label
        for flag, label in (
            (caps.supports_hook, "hook"),
            (caps.supports_resume, "resume"),
            (caps.supports_continue, "continue"),
        )
        if flag
    ]
    return caps.name, ", ".join(flags) or "none"


def status_main() -> None:
    load_ccgram_env()
    from . import __version__

    provider_name, cap_flags = _capability_summary()
    config_dir = ccgram_dir()
    mux_name = _active_multiplexer_name()
    session_name = tmux_session_name()

    state = _read_json(config_dir / "state.json")
    session_map = _read_json(config_dir / "session_map.json")

    if mux_name == _HERDR_BACKEND:
        live_windows = _list_herdr_windows()
        backend_line = f"Herdr: {len(live_windows)} pane(s)"
    else:
        live_windows = _list_tmux_windows(session_name)
        backend_line = f"Tmux session: {session_name} ({len(live_windows)} windows)"

    thread_bindings = state.get("thread_bindings", {})
    display_names = state.get("window_display_names", {})
    bound_windows: dict[str, tuple[int, int]] = {}
    for user_id_str, bindings in thread_bindings.items():
        for thread_id_str, window_id in bindings.items():
            bound_windows[window_id] = (int(thread_id_str), int(user_id_str))

    prefix = session_map_prefix_for(mux_name, session_name)
    monitored = sum(1 for k in session_map if k.startswith(prefix))

    print(f"ccgram {__version__}")
    print(f"Provider: {provider_name} ({cap_flags})")
    print(backend_line)
    print(f"Monitored sessions: {monitored}")

    if not live_windows and not bound_windows:
        return

    print()

    shown_ids: set[str] = set()
    for w in live_windows:
        wid = w["id"]
        name = display_names.get(wid, w["name"])
        shown_ids.add(wid)

        if wid in bound_windows:
            thread_id, user_id = bound_windows[wid]
            print(
                f"  {wid:<5} {name:<16} -> topic {thread_id} (user {user_id})   alive"
            )
        else:
            print(f"  {wid:<5} {name:<16}                              (unbound)")

    for wid, (thread_id, user_id) in bound_windows.items():
        if wid not in shown_ids:
            name = display_names.get(wid, wid)
            print(f"  {wid:<5} {name:<16} -> topic {thread_id} (user {user_id})   dead")

    sys.exit(0)
