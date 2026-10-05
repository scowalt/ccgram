import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from collections.abc import Callable

from .providers import resolve_capabilities
from .telegram_draft import draft_unavailable_reason, is_draft_unavailable
from .utils import ccgram_dir, load_ccgram_env, tmux_session_name

_PASS = "pass"
_FAIL = "fail"
_WARN = "warn"

_SYMBOLS = {_PASS: "\u2713", _FAIL: "\u2717", _WARN: "\u26a0"}

_TMUX_FORMAT_PARTS = 2
_MAIN_WINDOW_NAME = "__main__"

_MULTIPLEXER_ENV = "CCGRAM_MULTIPLEXER"
_DEFAULT_MULTIPLEXER = "tmux"
_HERDR_BACKEND = "herdr"


def _active_multiplexer_name() -> str:
    return os.environ.get(_MULTIPLEXER_ENV, _DEFAULT_MULTIPLEXER)


def _check_multiplexer() -> tuple[str, str]:
    name = _active_multiplexer_name()
    from .multiplexer import multiplexer_names

    names = multiplexer_names()
    if name not in names:
        available = ", ".join(sorted(names)) or "(none)"
        return _FAIL, f"Unknown multiplexer {name!r}. Available: {available}"
    return _PASS, f"multiplexer backend: {name}"


def _check_herdr() -> tuple[str, str]:
    if not shutil.which(_HERDR_BACKEND):
        return _FAIL, "herdr not found in PATH"
    socket = os.environ.get("HERDR_SOCKET_PATH", "")
    from .multiplexer import get_multiplexer

    backend = get_multiplexer(_HERDR_BACKEND)
    try:
        asyncio.run(backend.ensure_session())
    except Exception as exc:  # noqa: BLE001
        return _FAIL, f"herdr server unreachable: {exc}"
    where = f" ({socket})" if socket else " (default socket)"
    return _PASS, f"herdr server reachable{where}"


def _all_hook_commands(settings: dict) -> list[str]:
    return [
        hook_config.get("command", "")
        for groups in settings.get("hooks", {}).values()
        if isinstance(groups, list)
        for group in groups
        if isinstance(group, dict)
        for hook_config in group.get("hooks", [])
        if isinstance(hook_config, dict)
    ]


def _check_herdr_hook_coexistence() -> tuple[str, str]:
    from .hook import _claude_settings_file

    settings_file = _claude_settings_file()
    if not settings_file.exists():
        return _WARN, f"hook coexistence: {settings_file} missing"
    try:
        settings = json.loads(settings_file.read_text())
    except json.JSONDecodeError, OSError:
        return _WARN, "hook coexistence: settings.json unreadable"
    commands = _all_hook_commands(settings)
    has_ccgram = any("ccgram" in c for c in commands)
    has_herdr = any(_HERDR_BACKEND in c for c in commands)
    if has_ccgram and has_herdr:
        return _PASS, "ccgram + herdr Claude hooks coexist"
    if has_ccgram:
        return (
            _WARN,
            "herdr Claude hook absent "
            "(run `herdr integration install claude` for herdr's own hook)",
        )
    return _FAIL, "ccgram Claude hook missing from settings.json"


def _print_check(status: str, message: str) -> None:
    sym = _SYMBOLS.get(status, "?")
    print(f"  {sym} {message}")


def _check_tmux() -> tuple[str, str]:
    path = shutil.which("tmux")
    if not path:
        return _FAIL, "tmux not found in PATH"
    try:
        result = subprocess.run(
            ["tmux", "-V"], capture_output=True, text=True, timeout=5
        )
        version = result.stdout.strip()
        return _PASS, f"{version} found"
    except OSError, subprocess.TimeoutExpired:
        return _PASS, "tmux found (version unknown)"


def _check_provider_command(provider_name: str) -> tuple[str, str]:
    from ccgram.providers import resolve_launch_command

    cmd = resolve_launch_command(provider_name)
    executable = cmd.split()[0]
    path = shutil.which(executable)
    if path:
        label = cmd if cmd != executable else executable
        return _PASS, f"{label} found at {path}"
    return _FAIL, f"'{executable}' not found in PATH"


def _check_tmux_session() -> tuple[str, str]:
    session_name = tmux_session_name()
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return _PASS, f'tmux session "{session_name}" exists'
        return _FAIL, f'tmux session "{session_name}" not found'
    except OSError, subprocess.TimeoutExpired:
        return _FAIL, "cannot connect to tmux server"


def _resolve_hook_check_config(
    provider_name: str,
) -> tuple[Path, tuple[str, ...], Callable[[str], bool] | None] | None:
    from .hook import (
        _CODEX_HOOK_EVENTS,
        _GEMINI_HOOK_EVENTS,
        _HOOK_EVENT_TYPES,
        _claude_settings_file,
        _codex_hooks_file,
        _gemini_settings_file,
        _json_hook_command_predicate,
    )

    if provider_name == "codex":
        return (
            _codex_hooks_file(),
            _CODEX_HOOK_EVENTS,
            _json_hook_command_predicate("codex"),
        )
    if provider_name == "gemini":
        return (
            _gemini_settings_file(),
            _GEMINI_HOOK_EVENTS,
            _json_hook_command_predicate("gemini"),
        )
    if provider_name == "claude":
        return _claude_settings_file(), _HOOK_EVENT_TYPES, None
    return None


def _scan_json_hook_events(
    settings: dict, events: tuple[str, ...], predicate: Callable[[str], bool]
) -> dict[str, bool]:
    return {
        event_type: any(
            isinstance(hook_config, dict) and predicate(hook_config.get("command", ""))
            for group in settings.get("hooks", {}).get(event_type, [])
            if isinstance(group, dict)
            for hook_config in group.get("hooks", [])
        )
        for event_type in events
    }


def _check_hooks(provider_name: str = "claude") -> tuple[str, str, dict[str, bool]]:
    if provider_name == "pi":
        return _PASS, "Pi hooks are managed by hook-runner extension", {}

    resolved = _resolve_hook_check_config(provider_name)
    if resolved is None:
        return _FAIL, f"unknown provider for hook check: {provider_name}", {}
    settings_file, events, predicate = resolved

    all_missing = {event: False for event in events}

    if not settings_file.exists():
        return _FAIL, f"hooks not installed ({settings_file} missing)", all_missing
    try:
        settings = json.loads(settings_file.read_text())
    except json.JSONDecodeError, OSError:
        return _FAIL, "hooks not installed (settings.json unreadable)", all_missing

    if predicate is None:
        from .hook import get_installed_events

        event_status = get_installed_events(settings)
    else:
        event_status = _scan_json_hook_events(settings, events, predicate)
    installed = [e for e, v in event_status.items() if v]
    missing = [e for e, v in event_status.items() if not v]

    if not missing:
        return _PASS, f"all {len(installed)} hook events installed", event_status
    if not installed:
        return _FAIL, "no hook events installed", event_status
    return (
        _WARN,
        f"{len(installed)} installed, {len(missing)} missing: {', '.join(missing)}",
        event_status,
    )


def _check_config_dir() -> tuple[str, str]:
    config_dir = ccgram_dir()
    if config_dir.is_dir():
        return _PASS, f"config dir {config_dir} exists"
    return _FAIL, f"config dir {config_dir} not found"


def _check_bot_token() -> tuple[str, str]:
    load_ccgram_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if token:
        return _PASS, "TELEGRAM_BOT_TOKEN set"
    return _FAIL, "TELEGRAM_BOT_TOKEN not set"


def _check_allowed_users() -> tuple[str, str]:
    users_str = os.environ.get("ALLOWED_USERS", "")
    if not users_str:
        return _FAIL, "ALLOWED_USERS not set"
    try:
        users = [int(u.strip()) for u in users_str.split(",") if u.strip()]
        return _PASS, f"ALLOWED_USERS: {len(users)} user(s)"
    except ValueError:
        return _FAIL, "ALLOWED_USERS contains non-numeric values"


def _check_draft_streaming() -> tuple[str, str]:
    if is_draft_unavailable():
        reason = draft_unavailable_reason() or "Bot API <9.5"
        return _WARN, f"[draft-streaming] degraded — {reason}"
    return _PASS, "[draft-streaming] untested (probes lazily on first stream)"


def _check_events_file() -> tuple[str, str]:
    config_dir = ccgram_dir()
    events_file = config_dir / "events.jsonl"
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        with open(events_file, "a") as f:
            f.write("")
        return _PASS, f"events file {events_file} writable"
    except OSError as e:
        return _WARN, f"events file not writable: {e}"


def _list_live_windows(session_name: str) -> dict[str, str]:
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
            return {}
    except OSError, subprocess.TimeoutExpired:
        return {}

    windows: dict[str, str] = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == _TMUX_FORMAT_PARTS and parts[1] != _MAIN_WINDOW_NAME:
            windows[parts[0]] = parts[1]
    return windows


def _get_known_window_ids(config_dir: Path, session_name: str) -> set[str]:
    known: set[str] = set()

    state_file = config_dir / "state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            for bindings in state.get("thread_bindings", {}).values():
                known.update(bindings.values())
        except json.JSONDecodeError, OSError:
            pass

    session_map_file = config_dir / "session_map.json"
    prefix = f"{session_name}:"
    if session_map_file.exists():
        try:
            session_map = json.loads(session_map_file.read_text())
            for key in session_map:
                if key.startswith(prefix):
                    known.add(key[len(prefix) :])
        except json.JSONDecodeError, OSError:
            pass

    return known


def _find_orphaned_windows() -> list[tuple[str, str]]:
    session_name = tmux_session_name()
    live_windows = _list_live_windows(session_name)
    if not live_windows:
        return []

    known_ids = _get_known_window_ids(ccgram_dir(), session_name)
    return [(wid, wname) for wid, wname in live_windows.items() if wid not in known_ids]


def _run_check(check_fn: Callable[[], tuple[str, str]]) -> tuple[str, str, bool]:
    result = check_fn()
    status, msg = result[0], result[1]
    _print_check(status, msg)
    return status, msg, status == _FAIL


def _fix_hooks(
    event_status: dict[str, bool], fix: bool, provider_name: str = "claude"
) -> None:
    if not fix:
        return
    missing = [e for e, v in event_status.items() if not v]
    if not missing:
        return
    from .hook import _install_hook

    result = _install_hook(provider_name)
    if result == 0:
        _print_check(_PASS, "hooks installed (fixed)")
    else:
        _print_check(_FAIL, "failed to install hooks")


def _fix_orphans(orphans: list[tuple[str, str]], fix: bool) -> None:
    if not fix:
        return
    session_name = tmux_session_name()
    for wid, wname in orphans:
        try:
            subprocess.run(
                ["tmux", "kill-window", "-t", f"{session_name}:{wid}"],
                capture_output=True,
                timeout=5,
            )
            _print_check(_PASS, f"killed orphaned window {wid} ({wname})")
        except OSError, subprocess.TimeoutExpired:
            _print_check(_FAIL, f"failed to kill window {wid}")


def doctor_main(fix: bool = False) -> None:
    load_ccgram_env()

    caps = resolve_capabilities()
    has_failures = False

    print(f"Provider: {caps.name}")

    mux_name = _active_multiplexer_name()

    _, _, failed = _run_check(_check_multiplexer)
    has_failures = has_failures or failed

    _, _, failed = _run_check(lambda: _check_provider_command(caps.name))
    has_failures = has_failures or failed

    if mux_name == _HERDR_BACKEND:
        _, _, failed = _run_check(_check_herdr)
        has_failures = has_failures or failed
        _, _, failed = _run_check(_check_herdr_hook_coexistence)
        has_failures = has_failures or failed
    else:
        _, _, failed = _run_check(_check_tmux)
        has_failures = has_failures or failed
        _, _, failed = _run_check(_check_tmux_session)
        has_failures = has_failures or failed

    if caps.supports_hook:
        hook_status, hook_msg, event_status = _check_hooks(caps.name)
        _print_check(hook_status, hook_msg)
        if hook_status == _FAIL:
            has_failures = True
        _fix_hooks(event_status, fix, caps.name)
    else:
        _print_check(_PASS, f"hook check skipped ({caps.name} has no hook support)")

    for check_fn in (_check_config_dir, _check_bot_token, _check_allowed_users):
        _, _, failed = _run_check(check_fn)
        has_failures = has_failures or failed

    _, _, failed = _run_check(_check_events_file)
    has_failures = has_failures or failed

    _run_check(_check_draft_streaming)

    if mux_name != _HERDR_BACKEND:
        orphans = _find_orphaned_windows()
        if orphans:
            names = ", ".join(f"{wid} ({wname})" for wid, wname in orphans)
            _print_check(_WARN, f"{len(orphans)} orphaned window(s): {names}")
            _fix_orphans(orphans, fix)
        else:
            _print_check(_PASS, "no orphaned windows")

    sys.exit(1 if has_failures else 0)
