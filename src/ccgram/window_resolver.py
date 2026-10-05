from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class LiveWindow:
    window_id: str
    window_name: str


def is_window_id(key: str) -> bool:
    return key.startswith("@") and len(key) > 1 and key[1:].isdigit()


EMDASH_SESSION_PREFIX = "emdash-"


def is_foreign_window(window_id: str) -> bool:
    return ":" in window_id and not window_id.startswith("@")


def session_map_prefix_for(mux_name: str, session_name: str) -> str:
    if mux_name == "tmux":
        return f"{session_name}:"
    return f"{mux_name}:"


def _resolve_window_states(
    window_states: dict,
    window_display_names: dict,
    live_by_name: dict[str, str],
    live_ids: set[str],
) -> bool:
    changed = False
    new_states: dict = {}
    for key, ws in window_states.items():
        if is_foreign_window(key):
            new_states[key] = ws
            continue
        if is_window_id(key):
            if key in live_ids:
                new_states[key] = ws
            else:
                display = window_display_names.get(
                    key, getattr(ws, "window_name", "") or key
                )
                new_id = live_by_name.get(display)
                if new_id:
                    logger.debug("Re-resolved stale window_id %s -> %s", key, new_id)
                    new_states[new_id] = ws
                    ws.window_name = display
                    window_display_names[new_id] = display
                    window_display_names.pop(key, None)
                    changed = True
                else:
                    new_states[key] = ws
        else:
            new_id = live_by_name.get(key)
            if new_id:
                logger.debug("Migrating window_state key %s -> %s", key, new_id)
                ws.window_name = key
                new_states[new_id] = ws
                window_display_names[new_id] = key
                changed = True
            else:
                logger.debug("Dropping old-format window_state: %s", key)
                changed = True
    window_states.clear()
    window_states.update(new_states)
    return changed


def _resolve_thread_binding_value(
    val: str,
    *,
    user_id: int,
    thread_id: int,
    window_display_names: dict,
    live_by_name: dict[str, str],
    live_ids: set[str],
    reserved_window_ids: set[str],
) -> tuple[str | None, bool]:
    if is_foreign_window(val):
        return val, False
    if is_window_id(val):
        if val in live_ids:
            return val, False
        display = window_display_names.get(val, val)
        new_id = live_by_name.get(display)
        if not new_id:
            return val, False
        if new_id in reserved_window_ids:
            logger.debug(
                "Keeping stale thread binding %s because live window %s "
                "is already bound",
                val,
                new_id,
            )
            return val, False
        logger.debug("Re-resolved thread binding %s -> %s", val, new_id)
        reserved_window_ids.add(new_id)
        window_display_names[new_id] = display
        return new_id, True

    new_id = live_by_name.get(val)
    if new_id:
        if new_id in reserved_window_ids:
            logger.debug(
                "Dropping old-format thread binding %s because live window %s "
                "is already bound",
                val,
                new_id,
            )
            return None, True
        logger.debug("Migrating thread binding %s -> %s", val, new_id)
        reserved_window_ids.add(new_id)
        window_display_names[new_id] = val
        return new_id, True

    logger.debug(
        "Dropping old-format thread binding: user=%d, thread=%d, name=%s",
        user_id,
        thread_id,
        val,
    )
    return None, True


def _resolve_thread_bindings(
    thread_bindings: dict,
    window_display_names: dict,
    live_by_name: dict[str, str],
    live_ids: set[str],
) -> bool:
    changed = False
    for uid, bindings in thread_bindings.items():
        new_bindings: dict[int, str] = {}
        reserved_window_ids = {
            val
            for val in bindings.values()
            if is_foreign_window(val) or val in live_ids
        }
        for tid, val in bindings.items():
            new_val, value_changed = _resolve_thread_binding_value(
                val,
                user_id=uid,
                thread_id=tid,
                window_display_names=window_display_names,
                live_by_name=live_by_name,
                live_ids=live_ids,
                reserved_window_ids=reserved_window_ids,
            )
            changed |= value_changed
            if new_val is not None:
                new_bindings[tid] = new_val
        bindings.clear()
        bindings.update(new_bindings)

    empty_users = [uid for uid, b in thread_bindings.items() if not b]
    for uid in empty_users:
        del thread_bindings[uid]
    return changed


def _resolve_offsets(
    user_window_offsets: dict,
    window_display_names: dict,
    live_by_name: dict[str, str],
    live_ids: set[str],
) -> bool:
    changed = False
    for _uid, offsets in user_window_offsets.items():
        new_offsets: dict[str, int] = {}
        for key, offset in offsets.items():
            if is_foreign_window(key):
                new_offsets[key] = offset
                continue
            if is_window_id(key):
                if key in live_ids:
                    new_offsets[key] = offset
                elif new_id := live_by_name.get(window_display_names.get(key, key)):
                    new_offsets[new_id] = offset
                    changed = True
                else:
                    changed = True
            elif new_id := live_by_name.get(key):
                new_offsets[new_id] = offset
                changed = True
            else:
                changed = True
        offsets.clear()
        offsets.update(new_offsets)
    return changed


def resolve_stale_ids(
    live_windows: list[LiveWindow],
    window_states: dict,
    thread_bindings: dict,
    user_window_offsets: dict,
    window_display_names: dict,
    *,
    ids_stable: bool = True,
    live_session_ids: dict[str, str] | None = None,
) -> bool:
    if not ids_stable:
        return _resolve_by_session_id(
            live_windows,
            window_states,
            thread_bindings,
            user_window_offsets,
            window_display_names,
            live_session_ids or {},
        )

    live_by_name: dict[str, str] = {w.window_name: w.window_id for w in live_windows}
    live_ids: set[str] = {w.window_id for w in live_windows}

    changed = _resolve_window_states(
        window_states, window_display_names, live_by_name, live_ids
    )
    changed |= _resolve_thread_bindings(
        thread_bindings, window_display_names, live_by_name, live_ids
    )
    changed |= _resolve_offsets(
        user_window_offsets, window_display_names, live_by_name, live_ids
    )
    return changed


def _build_session_remap(
    live_windows: list[LiveWindow],
    window_states: dict,
    live_session_ids: dict[str, str],
) -> dict[str, str]:
    live_ids: set[str] = {w.window_id for w in live_windows}
    by_session: dict[str, str] = {}
    for wid, sid in live_session_ids.items():
        if sid and wid in live_ids:
            by_session.setdefault(sid, wid)

    remap: dict[str, str] = {}
    for old_id, ws in window_states.items():
        if old_id in live_ids:
            continue
        sid = getattr(ws, "session_id", "") or ""
        new_id = by_session.get(sid) if sid else None
        if new_id and new_id != old_id:
            remap[old_id] = new_id
    return remap


def _remap_window_states(
    window_states: dict, window_display_names: dict, remap: dict[str, str]
) -> bool:
    changed = False
    new_states: dict = {}
    for key, ws in window_states.items():
        new_id = remap.get(key)
        if new_id:
            logger.debug(
                "Re-resolved herdr window_id %s -> %s (session match)", key, new_id
            )
            new_states[new_id] = ws
            display = window_display_names.pop(key, None)
            if display is not None:
                window_display_names[new_id] = display
            changed = True
        else:
            new_states[key] = ws
    window_states.clear()
    window_states.update(new_states)
    return changed


def _remap_thread_bindings(thread_bindings: dict, remap: dict[str, str]) -> bool:
    changed = False
    for bindings in thread_bindings.values():
        for tid, val in list(bindings.items()):
            new_id = remap.get(val)
            if new_id:
                logger.debug("Re-resolved herdr thread binding %s -> %s", val, new_id)
                bindings[tid] = new_id
                changed = True
    return changed


def _remap_offsets(user_window_offsets: dict, remap: dict[str, str]) -> bool:
    changed = False
    for offsets in user_window_offsets.values():
        for key in list(offsets):
            new_id = remap.get(key)
            if new_id:
                offsets[new_id] = offsets.pop(key)
                changed = True
    return changed


def _resolve_by_session_id(
    live_windows: list[LiveWindow],
    window_states: dict,
    thread_bindings: dict,
    user_window_offsets: dict,
    window_display_names: dict,
    live_session_ids: dict[str, str],
) -> bool:
    remap = _build_session_remap(live_windows, window_states, live_session_ids)
    if not remap:
        return False
    changed = _remap_window_states(window_states, window_display_names, remap)
    changed |= _remap_thread_bindings(thread_bindings, remap)
    changed |= _remap_offsets(user_window_offsets, remap)
    return changed
