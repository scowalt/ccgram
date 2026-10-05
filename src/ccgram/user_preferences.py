from __future__ import annotations

import structlog
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

logger = structlog.get_logger()


class UserPreferences:
    def __init__(self, *, schedule_save: Callable[[], None]) -> None:
        self.user_dir_favorites: dict[int, dict[str, list[str]]] = {}
        self.user_window_offsets: dict[int, dict[str, int]] = {}
        self._schedule_save: Callable[[], None] = schedule_save

    def reset(self) -> None:
        self.user_dir_favorites.clear()
        self.user_window_offsets.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_window_offsets": {
                str(uid): offsets for uid, offsets in self.user_window_offsets.items()
            },
            "user_dir_favorites": {
                str(uid): favs for uid, favs in self.user_dir_favorites.items()
            },
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        self.user_window_offsets = {
            int(uid): offsets
            for uid, offsets in data.get("user_window_offsets", {}).items()
        }
        self.user_dir_favorites = {
            int(uid): favs for uid, favs in data.get("user_dir_favorites", {}).items()
        }

    def get_user_starred(self, user_id: int) -> list[str]:
        return list(self.user_dir_favorites.get(user_id, {}).get("starred", []))

    def get_user_mru(self, user_id: int) -> list[str]:
        return list(self.user_dir_favorites.get(user_id, {}).get("mru", []))

    def update_user_mru(self, user_id: int, path: str) -> None:
        resolved = str(Path(path).resolve())
        favs = self.user_dir_favorites.setdefault(user_id, {})
        mru: list[str] = favs.get("mru", [])
        mru = [resolved] + [p for p in mru if p != resolved]
        favs["mru"] = mru[:5]
        self._schedule_save()

    def toggle_user_star(self, user_id: int, path: str) -> bool:
        resolved = str(Path(path).resolve())
        favs = self.user_dir_favorites.setdefault(user_id, {})
        starred: list[str] = favs.get("starred", [])
        if resolved in starred:
            starred.remove(resolved)
            now_starred = False
        else:
            starred.append(resolved)
            now_starred = True
        favs["starred"] = starred
        self._schedule_save()
        return now_starred

    def get_user_window_offset(self, user_id: int, window_id: str) -> int | None:
        user_offsets = self.user_window_offsets.get(user_id)
        if user_offsets is None:
            return None
        return user_offsets.get(window_id)

    def update_user_window_offset(
        self, user_id: int, window_id: str, offset: int
    ) -> None:
        if user_id not in self.user_window_offsets:
            self.user_window_offsets[user_id] = {}
        self.user_window_offsets[user_id][window_id] = offset
        self._schedule_save()

    def prune_stale_offsets(self, known_window_ids: set[str]) -> bool:
        changed = False
        empty_users: list[int] = []
        pruned = 0
        for uid, offsets in self.user_window_offsets.items():
            stale = [wid for wid in offsets if wid not in known_window_ids]
            for wid in stale:
                logger.debug("Pruning stale offset: user %d, window %s", uid, wid)
                del offsets[wid]
                changed = True
                pruned += 1
            if not offsets:
                empty_users.append(uid)
        for uid in empty_users:
            del self.user_window_offsets[uid]
            changed = True
        if pruned:
            logger.info("Pruned %d stale window offset(s)", pruned)
        if changed:
            self._schedule_save()
        return changed


_active_prefs: UserPreferences | None = None


def get_user_preferences() -> UserPreferences:
    if _active_prefs is None:
        raise RuntimeError(
            "UserPreferences not yet wired. "
            "Instantiate SessionManager() before accessing user_preferences."
        )
    return _active_prefs


def install_user_preferences(prefs: UserPreferences) -> None:
    global _active_prefs
    _active_prefs = prefs


class _UserPreferencesProxy:
    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(get_user_preferences(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(get_user_preferences(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(get_user_preferences(), name)

    def __repr__(self) -> str:
        if _active_prefs is None:
            return "<UserPreferencesProxy unwired>"
        return f"<UserPreferencesProxy → {_active_prefs!r}>"


user_preferences: UserPreferences = cast("UserPreferences", _UserPreferencesProxy())
