from __future__ import annotations

import asyncio
import fcntl
import json
import os
import shutil
import time
import structlog
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import aiofiles

from .config import config
from .hooks.state_files import StateFileValidationError, parse_session_map_entry
from .utils import atomic_write_json, log_throttle_reset, log_throttled
from .window_resolver import is_window_id, session_map_prefix_for

logger = structlog.get_logger()

_DEFAULT_PRIMARY_SESSION_GRACE_SEC = 60.0


def _primary_session_grace_sec() -> float:
    raw = os.getenv("CCGRAM_NESTED_SESSION_GRACE_SEC")
    if raw is None:
        return _DEFAULT_PRIMARY_SESSION_GRACE_SEC
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "CCGRAM_NESTED_SESSION_GRACE_SEC must be a number, got %r; using default %.1f",
            raw,
            _DEFAULT_PRIMARY_SESSION_GRACE_SEC,
        )
        return _DEFAULT_PRIMARY_SESSION_GRACE_SEC


async def read_session_map_raw() -> dict[str, Any] | None:
    if not config.session_map_file.exists():
        return {}
    try:
        async with aiofiles.open(config.session_map_file, "r") as f:
            content = await f.read()
        return cast(dict[str, Any], json.loads(content))
    except json.JSONDecodeError, OSError:
        return None


def live_window_session_ids(
    raw: dict[str, Any], live_window_ids: set[str]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, info in raw.items():
        if not isinstance(info, dict):
            continue
        sid = info.get("session_id", "")
        if not sid:
            continue
        for wid in live_window_ids:
            if key == wid or key.endswith(f":{wid}"):
                result[wid] = sid
                break
    return result


def session_map_prefix() -> str:
    return session_map_prefix_for(config.multiplexer_name, config.tmux_session_name)


def is_backend_window_id(window_id: str) -> bool:
    if config.multiplexer_name == "tmux":
        return is_window_id(window_id)
    return bool(window_id)


def _transcript_mtime(transcript_path: str) -> float | None:
    if not transcript_path:
        return None
    try:
        return Path(transcript_path).stat().st_mtime
    except OSError:
        return None


def _transcript_is_fresh(transcript_path: str, *, now: float | None = None) -> bool:
    mtime = _transcript_mtime(transcript_path)
    if mtime is None:
        return False
    reference = time.time() if now is None else now
    return reference - mtime < _primary_session_grace_sec()


def _prefer_existing_primary(
    window_id: str,
    incoming: dict[str, Any],
) -> dict[str, str] | None:
    if config.multiplexer_name != "tmux":
        return None

    from .window_state_store import is_window_store_wired, window_store

    if not is_window_store_wired():
        return None
    state = window_store.window_states.get(window_id)
    if not state or not state.session_id:
        return None

    incoming_sid = incoming.get("session_id", "")
    if not incoming_sid or incoming_sid == state.session_id:
        return None

    existing_mtime = _transcript_mtime(state.transcript_path)
    incoming_mtime = _transcript_mtime(incoming.get("transcript_path", ""))
    existing_is_fresh = _transcript_is_fresh(state.transcript_path)
    existing_is_newer = existing_mtime is not None and (
        incoming_mtime is None or existing_mtime >= incoming_mtime
    )
    if not existing_is_fresh and not existing_is_newer:
        return None

    log_throttled(
        logger,
        f"preserve-primary:{window_id}",
        "Preserving primary session for window_id %s: existing %s, incoming %s treated as nested",
        window_id,
        state.session_id,
        incoming_sid,
    )
    return {
        "session_id": state.session_id,
        "cwd": state.cwd,
        "window_name": incoming.get("window_name", "") or state.window_name,
        "transcript_path": state.transcript_path,
        "provider_name": incoming.get("provider_name", "") or state.provider_name,
    }


def effective_session_map_info(
    window_id: str,
    info: dict[str, Any],
) -> dict[str, str]:
    preferred = _prefer_existing_primary(window_id, info)
    if preferred is not None:
        return preferred
    return {
        "session_id": info.get("session_id", ""),
        "cwd": info.get("cwd", ""),
        "window_name": info.get("window_name", ""),
        "transcript_path": info.get("transcript_path", ""),
        "provider_name": info.get("provider_name", ""),
    }


def parse_session_map(raw: dict[str, Any], prefix: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for key, info in raw.items():
        if not key.startswith(prefix):
            continue
        window_name = key[len(prefix) :]
        if not isinstance(info, dict):
            continue
        try:
            parse_session_map_entry(info)
        except StateFileValidationError as exc:
            logger.debug("Skipping invalid session_map entry %s: %s", key, exc)
            continue
        effective = effective_session_map_info(window_name, info)
        if effective["session_id"]:
            result[window_name] = effective
    return result


class SessionMapSync:
    def __init__(self, *, schedule_save: Callable[[], None]) -> None:
        self._schedule_save: Callable[[], None] = schedule_save

    async def load_session_map(self, raw: dict[str, Any] | None = None) -> None:
        if raw is None:
            raw = await read_session_map_raw()
        if not raw:
            return
        session_map = raw

        prefix = session_map_prefix()
        valid_wids, old_format_sids, old_format_keys, changed = (
            self._process_session_map_entries(session_map, prefix)
        )
        changed |= self._remove_stale_window_states(valid_wids, old_format_sids)
        self._purge_old_format_keys(session_map, old_format_keys)

        if changed:
            self._schedule_save()

    def _process_session_map_entries(
        self,
        session_map: dict[str, Any],
        prefix: str,
    ) -> tuple[set[str], set[str], list[str], bool]:
        valid_wids: set[str] = set()
        old_format_sids: set[str] = set()
        old_format_keys: list[str] = []
        changed = False

        for key, info in session_map.items():
            if not isinstance(info, dict):
                continue
            if not key.startswith(prefix):
                continue
            window_id = key[len(prefix) :]
            if not is_backend_window_id(window_id):
                sid = info.get("session_id", "")
                if sid:
                    old_format_sids.add(sid)
                old_format_keys.append(key)
                continue
            valid_wids.add(window_id)
            try:
                parse_session_map_entry(info)
            except StateFileValidationError as exc:
                logger.debug("Skipping invalid session_map entry %s: %s", key, exc)
                continue
            if self._sync_window_from_session_map(window_id, info):
                changed = True

        return valid_wids, old_format_sids, old_format_keys, changed

    def _remove_stale_window_states(
        self,
        valid_wids: set[str],
        old_format_sids: set[str],
    ) -> bool:
        from .thread_router import thread_router

        from .window_state_store import window_store

        bound_wids = {
            wid
            for user_bindings in thread_router.thread_bindings.values()
            for wid in user_bindings.values()
            if wid
        }
        stale_wids = [
            w
            for w in window_store.iter_window_ids()
            if (
                w
                and w not in valid_wids
                and w not in bound_wids
                and window_store.get_session_id_for_window(w) not in old_format_sids
            )
        ]
        for wid in stale_wids:
            logger.info("Removing stale window_state: %s", wid)
            window_store.remove_window(wid)
        return bool(stale_wids)

    def _purge_old_format_keys(
        self,
        session_map: dict[str, Any],
        old_format_keys: list[str],
    ) -> None:
        if not old_format_keys:
            return
        for key in old_format_keys:
            logger.info("Removing old-format session_map key: %s", key)
            del session_map[key]
        atomic_write_json(config.session_map_file, session_map)

    async def wait_for_session_map_entry(
        self, window_id: str, timeout: float = 5.0, interval: float = 0.5
    ) -> bool:
        logger.debug(
            "Waiting for session_map entry: window_id=%s, timeout=%.1f",
            window_id,
            timeout,
        )
        key = f"{session_map_prefix()}{window_id}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                if config.session_map_file.exists():
                    async with aiofiles.open(config.session_map_file, "r") as f:
                        content = await f.read()
                    session_map = json.loads(content)
                    info = session_map.get(key, {})
                    if info.get("session_id"):
                        logger.debug(
                            "session_map entry found for window_id %s", window_id
                        )
                        await self.load_session_map()
                        return True
            except json.JSONDecodeError, OSError:
                pass
            await asyncio.sleep(interval)
        logger.warning(
            "Timed out waiting for session_map entry: window_id=%s", window_id
        )
        return False

    def prune_session_map(self, live_window_ids: set[str]) -> None:
        from .window_state_store import window_store

        if not config.session_map_file.exists():
            return
        try:
            raw = json.loads(config.session_map_file.read_text())
        except json.JSONDecodeError, OSError:
            return

        prefix = session_map_prefix()
        dead_entries: list[tuple[str, str]] = []
        for key in raw:
            if not key.startswith(prefix):
                continue
            window_id = key[len(prefix) :]
            if is_backend_window_id(window_id) and window_id not in live_window_ids:
                dead_entries.append((key, window_id))

        if not dead_entries:
            return

        changed_state = False
        for key, window_id in dead_entries:
            logger.info(
                "Pruning dead session_map entry: %s (window %s)", key, window_id
            )
            del raw[key]
            log_throttle_reset(f"preserve-primary:{window_id}")
            if window_store.has_window(window_id):
                window_store.remove_window(window_id)
                changed_state = True

        atomic_write_json(config.session_map_file, raw)
        if changed_state:
            self._schedule_save()

    def register_hookless_session(
        self,
        window_id: str,
        session_id: str,
        cwd: str,
        transcript_path: str,
        provider_name: str,
    ) -> None:
        from .window_state_store import window_store

        state = window_store.get_window_state(window_id)
        state.session_id = session_id
        state.cwd = cwd
        state.transcript_path = transcript_path
        state.provider_name = provider_name
        self._schedule_save()

    def write_hookless_session_map(
        self,
        window_id: str,
        session_id: str,
        cwd: str,
        transcript_path: str,
        provider_name: str,
    ) -> None:
        from .thread_router import thread_router

        map_file = config.session_map_file
        map_file.parent.mkdir(parents=True, exist_ok=True)
        window_key = f"{session_map_prefix()}{window_id}"
        lock_path = map_file.with_suffix(".lock")
        try:
            with open(lock_path, "w") as lock_f:
                fcntl.flock(lock_f, fcntl.LOCK_EX)
                try:
                    session_map: dict[str, Any] = {}
                    if map_file.exists():
                        try:
                            parsed = json.loads(map_file.read_text())
                            if isinstance(parsed, dict):
                                session_map = parsed
                        except json.JSONDecodeError:
                            backup = map_file.with_suffix(".json.corrupt")
                            try:
                                shutil.copy2(map_file, backup)
                                logger.warning(
                                    "Corrupted session_map.json backed up to %s",
                                    backup,
                                )
                            except OSError:
                                logger.warning(
                                    "Corrupted session_map.json (backup failed)"
                                )
                        except OSError:
                            logger.warning(
                                "Failed to read session_map.json for hookless write"
                            )
                    display_name = thread_router.get_display_name(window_id)
                    from .hooks.state_files import serialize_session_map_entry

                    session_map[window_key] = serialize_session_map_entry(
                        session_id, cwd, display_name, transcript_path, provider_name
                    )
                    atomic_write_json(map_file, session_map)
                    logger.info(
                        "Registered hookless session: %s -> session_id=%s, cwd=%s",
                        window_key,
                        session_id,
                        cwd,
                    )
                finally:
                    fcntl.flock(lock_f, fcntl.LOCK_UN)
        except OSError:
            logger.exception("Failed to write session_map for hookless session")

    def clear_session_map_entry(self, window_id: str) -> None:
        if not config.session_map_file.exists():
            return
        lock_path = config.session_map_file.with_suffix(".lock")
        try:
            with open(lock_path, "w") as lock_f:
                fcntl.flock(lock_f, fcntl.LOCK_EX)
                try:
                    raw = json.loads(config.session_map_file.read_text())
                    key = f"{session_map_prefix()}{window_id}"
                    if key in raw:
                        del raw[key]
                        atomic_write_json(config.session_map_file, raw)
                        logger.debug("Cleared session_map entry for %s", window_id)
                except json.JSONDecodeError, OSError:
                    return
                finally:
                    fcntl.flock(lock_f, fcntl.LOCK_UN)
        except OSError as exc:
            logger.warning(
                "Failed to lock session_map for clearing %s: %s", window_id, exc
            )

    def _sync_window_from_session_map(
        self,
        window_id: str,
        info: dict[str, Any],
    ) -> bool:
        from .thread_router import thread_router

        from .window_state_store import window_store

        effective = effective_session_map_info(window_id, info)
        new_sid = effective["session_id"]
        if not new_sid:
            return False
        new_cwd = effective["cwd"]
        new_wname = effective["window_name"]
        new_transcript = effective["transcript_path"]
        changed = False

        state = window_store.get_window_state(window_id)
        if state.session_id != new_sid or state.cwd != new_cwd:
            logger.info(
                "Session map: window_id %s updated sid=%s, cwd=%s",
                window_id,
                new_sid,
                new_cwd,
            )
            state.session_id = new_sid
            state.cwd = new_cwd
            changed = True
        if new_transcript and state.transcript_path != new_transcript:
            state.transcript_path = new_transcript
            changed = True
        new_provider = effective["provider_name"].lower()
        path_for_inference = new_transcript or state.transcript_path
        if new_provider and path_for_inference:
            from .providers import detect_provider_from_transcript_path

            inferred = detect_provider_from_transcript_path(path_for_inference)
            if inferred and inferred != new_provider:
                new_provider = inferred
        if new_provider and state.provider_name != new_provider:
            logger.warning(
                "Corrected provider for %s: state=%s -> %s "
                "(session_map claimed %s; transcript_path=%s)",
                window_id,
                state.provider_name,
                new_provider,
                effective["provider_name"].lower(),
                path_for_inference,
            )
            state.provider_name = new_provider
            changed = True
        if (
            new_wname
            and thread_router.get_display_name(window_id) == window_id
            and not state.window_name
        ):
            state.window_name = new_wname
            thread_router.set_display_name(window_id, new_wname)
            changed = True
        return changed


_active_sync: SessionMapSync | None = None


def get_session_map_sync() -> SessionMapSync:
    if _active_sync is None:
        raise RuntimeError(
            "SessionMapSync not yet wired. "
            "Instantiate SessionManager() before accessing session_map_sync."
        )
    return _active_sync


def install_session_map_sync(sync: SessionMapSync) -> None:
    global _active_sync
    _active_sync = sync


class _SessionMapSyncProxy:
    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(get_session_map_sync(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(get_session_map_sync(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(get_session_map_sync(), name)

    def __repr__(self) -> str:
        if _active_sync is None:
            return "<SessionMapSyncProxy unwired>"
        return f"<SessionMapSyncProxy → {_active_sync!r}>"


session_map_sync: SessionMapSync = cast("SessionMapSync", _SessionMapSyncProxy())
