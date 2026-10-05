import json
import structlog
from dataclasses import dataclass, field
from typing import Any

from .config import config
from .session_map import (
    SessionMapSync,
    install_session_map_sync,
    is_backend_window_id,
    live_window_session_ids,
    read_session_map_raw,
    session_map_prefix,
    session_map_sync,
)
from .state_persistence import StatePersistence
from .multiplexer import multiplexer as tmux_manager
from .multiplexer.reconciliation import list_windows_for_reconciliation
from .thread_router import ThreadRouter, install_thread_router, thread_router
from .user_preferences import (
    UserPreferences,
    install_user_preferences,
    user_preferences,
)
from .window_view import WindowView
from .window_state_ports import identity_state as _identity_state
from .window_state_ports import lifecycle_state as _lifecycle_state
from .window_state_ports import tool_state as _tool_state
from .window_state_ports import worktree_state as _worktree_state
from .window_state_store import (
    WindowState,
    WindowStateStore,
    install_window_store,
    window_store,
)

logger = structlog.get_logger()


@dataclass
class AuditIssue:
    category: str
    detail: str
    fixable: bool


@dataclass
class AuditResult:
    issues: list[AuditIssue]
    total_bindings: int
    live_binding_count: int

    @property
    def fixable_count(self) -> int:
        return sum(1 for i in self.issues if i.fixable)

    @property
    def has_issues(self) -> bool:
        return len(self.issues) > 0


@dataclass
class SessionManager:
    _persistence: StatePersistence = field(default=None, repr=False, init=False)  # type: ignore[assignment]

    @property
    def window_states(self) -> dict[str, WindowState]:
        return window_store.window_states

    @property
    def thread_bindings(self) -> dict[int, dict[int, str]]:
        return thread_router.thread_bindings

    @property
    def group_chat_ids(self) -> dict[str, int]:
        return thread_router.group_chat_ids

    @property
    def window_display_names(self) -> dict[str, str]:
        return thread_router.window_display_names

    def __post_init__(self) -> None:
        self._persistence = StatePersistence(config.state_file, self._serialize_state)
        self._window_store = WindowStateStore(
            schedule_save=self._save_state,
            on_hookless_provider_switch=self._clear_session_map_entry,
        )
        install_window_store(self._window_store)
        self._thread_router = ThreadRouter(
            schedule_save=self._save_state,
            has_window_state=self._window_store.has_window,
        )
        install_thread_router(self._thread_router)
        self._user_preferences = UserPreferences(schedule_save=self._save_state)
        install_user_preferences(self._user_preferences)
        self._session_map_sync = SessionMapSync(schedule_save=self._save_state)
        install_session_map_sync(self._session_map_sync)
        self._load_state()

    def _serialize_state(self) -> dict[str, Any]:
        result = {"window_states": window_store.to_dict()}
        result.update(user_preferences.to_dict())
        result.update(thread_router.to_dict())
        return result

    def _save_state(self) -> None:
        self._persistence.schedule_save()

    def flush_state(self) -> None:
        self._persistence.flush()

    def _is_window_id(self, key: str) -> bool:
        return is_backend_window_id(key)

    def _load_state(self) -> None:
        state = self._persistence.load()
        if not state:
            return

        window_store.from_dict(state.get("window_states", {}))

        user_preferences.from_dict(state)

        thread_router.from_dict(state)

        needs_migration = False
        for k in window_store.window_states:
            if not self._is_window_id(k):
                needs_migration = True
                break
        if not needs_migration:
            for bindings in thread_router.thread_bindings.values():
                for wid in bindings.values():
                    if not self._is_window_id(wid):
                        needs_migration = True
                        break
                if needs_migration:
                    break

        if needs_migration:
            logger.info(
                "Detected old-format state (window_name keys), "
                "will re-resolve on startup"
            )

    async def resolve_stale_ids(self) -> None:
        from .window_resolver import LiveWindow, resolve_stale_ids as _resolve

        windows = await list_windows_for_reconciliation(tmux_manager)
        if windows is None:
            logger.warning(
                "Startup window reconciliation skipped: multiplexer listing unavailable"
            )
            return

        live = [
            LiveWindow(window_id=w.window_id, window_name=w.window_name)
            for w in windows
        ]

        caps = tmux_manager.capabilities
        live_session_ids: dict[str, str] | None = None
        if not caps.ids_stable_across_restart:
            raw = await read_session_map_raw() or {}
            live_session_ids = live_window_session_ids(
                raw, {w.window_id for w in windows}
            )

        changed = _resolve(
            live,
            self.window_states,
            thread_router.thread_bindings,
            user_preferences.user_window_offsets,
            thread_router.window_display_names,
            ids_stable=caps.ids_stable_across_restart,
            live_session_ids=live_session_ids,
        )

        if changed:
            thread_router._rebuild_reverse_index()
            self._save_state()
            logger.info("Startup re-resolution complete")

        live_ids = {w.window_id for w in live}
        session_map_sync.prune_session_map(live_ids)

        live_pairs = [(w.window_id, w.window_name) for w in live]
        self.sync_display_names(live_pairs)

        self.prune_stale_state(live_ids, skip_chat_ids=True)

    def set_display_name(self, window_id: str, window_name: str) -> None:
        thread_router.set_display_name(window_id, window_name)
        ws = self.window_states.get(window_id)
        if ws:
            ws.window_name = window_name

    def sync_display_names(self, live_windows: list[tuple[str, str]]) -> bool:
        router_changed = thread_router.sync_display_names(live_windows)
        ws_changed = False
        for window_id, window_name in live_windows:
            ws = self.window_states.get(window_id)
            if ws and ws.window_name != window_name:
                ws.window_name = window_name
                ws_changed = True
        if ws_changed and not router_changed:
            self._save_state()
        return router_changed or ws_changed

    def prune_stale_state(
        self, live_window_ids: set[str], *, skip_chat_ids: bool = False
    ) -> bool:
        in_use = set(self.window_states.keys())
        for bindings in thread_router.thread_bindings.values():
            in_use.update(bindings.values())

        stale_display = [
            wid
            for wid in thread_router.window_display_names
            if wid not in live_window_ids and wid not in in_use
        ]

        bound_keys: set[str] = set()
        for user_id, bindings in thread_router.thread_bindings.items():
            for thread_id in bindings:
                bound_keys.add(f"{user_id}:{thread_id}")

        stale_chat = (
            []
            if skip_chat_ids
            else [k for k in thread_router.group_chat_ids if k not in bound_keys]
        )

        all_known = live_window_ids | in_use
        offsets_changed = user_preferences.prune_stale_offsets(all_known)

        if not stale_display and not stale_chat:
            return offsets_changed

        for wid in stale_display:
            name = thread_router.pop_display_name(wid)
            logger.debug("Pruning stale display name: %s (%s)", wid, name)
        for key in stale_chat:
            logger.debug("Pruning stale group_chat_id: %s", key)
            del thread_router.group_chat_ids[key]
        logger.info(
            "Pruned stale state: %d display name(s), %d group chat id(s)",
            len(stale_display),
            len(stale_chat),
        )

        self._save_state()
        return True

    def _get_session_map_window_ids(self) -> set[str]:
        if not config.session_map_file.exists():
            return set()
        try:
            raw = json.loads(config.session_map_file.read_text())
        except json.JSONDecodeError, OSError:
            return set()
        prefix = session_map_prefix()
        result: set[str] = set()
        for key in raw:
            if key.startswith(prefix):
                wid = key[len(prefix) :]
                if self._is_window_id(wid):
                    result.add(wid)
        return result

    def audit_state(
        self,
        live_window_ids: set[str],
        live_windows: list[tuple[str, str]],
    ) -> AuditResult:
        issues: list[AuditIssue] = []

        bound_window_ids: set[str] = set()
        total_bindings = 0
        live_binding_count = 0
        for _uid, bindings in thread_router.thread_bindings.items():
            for _tid, wid in bindings.items():
                total_bindings += 1
                bound_window_ids.add(wid)
                if wid in live_window_ids:
                    live_binding_count += 1

        session_map_wids = self._get_session_map_window_ids()

        for uid, bindings in thread_router.thread_bindings.items():
            for tid, wid in bindings.items():
                if wid not in live_window_ids:
                    display = thread_router.get_display_name(wid)
                    issues.append(
                        AuditIssue(
                            category="ghost_binding",
                            detail=f"user:{uid} thread:{tid} window:{wid} ({display})",
                            fixable=True,
                        )
                    )

        in_use = set(self.window_states.keys()) | bound_window_ids
        for wid in thread_router.window_display_names:
            if wid not in live_window_ids and wid not in in_use:
                name = thread_router.get_display_name(wid)
                issues.append(
                    AuditIssue(
                        category="orphaned_display_name",
                        detail=f"{wid} ({name})",
                        fixable=True,
                    )
                )

        bound_keys: set[str] = set()
        for user_id, bindings in thread_router.thread_bindings.items():
            for thread_id in bindings:
                bound_keys.add(f"{user_id}:{thread_id}")
        for key in thread_router.group_chat_ids:
            if key not in bound_keys:
                issues.append(
                    AuditIssue(
                        category="orphaned_group_chat_id",
                        detail=f"key {key}",
                        fixable=True,
                    )
                )

        for wid in self.window_states:
            if (
                wid not in session_map_wids
                and wid not in bound_window_ids
                and wid not in live_window_ids
            ):
                display = self.window_states[wid].window_name or wid
                issues.append(
                    AuditIssue(
                        category="stale_window_state",
                        detail=f"{wid} ({display})",
                        fixable=True,
                    )
                )

        known_wids = live_window_ids | bound_window_ids | set(self.window_states.keys())
        for uid, offsets in user_preferences.user_window_offsets.items():
            for wid in offsets:
                if wid not in known_wids:
                    issues.append(
                        AuditIssue(
                            category="stale_offset",
                            detail=f"user {uid}, window {wid}",
                            fixable=True,
                        )
                    )

        for wid, tmux_name in live_windows:
            stored_name = thread_router.window_display_names.get(wid)
            if stored_name and stored_name != tmux_name:
                issues.append(
                    AuditIssue(
                        category="display_name_drift",
                        detail=f"{wid}: stored={stored_name!r} tmux={tmux_name!r}",
                        fixable=True,
                    )
                )

        known_wids = session_map_wids | set(self.window_states.keys())
        for wid in live_window_ids:
            if wid not in bound_window_ids and wid in known_wids:
                name = dict(live_windows).get(wid, wid)
                issues.append(
                    AuditIssue(
                        category="orphaned_window",
                        detail=f"{wid} ({name})",
                        fixable=True,
                    )
                )

        return AuditResult(
            issues=issues,
            total_bindings=total_bindings,
            live_binding_count=live_binding_count,
        )

    def prune_stale_window_states(self, live_window_ids: set[str]) -> bool:
        session_map_wids = self._get_session_map_window_ids()
        bound_window_ids: set[str] = set()
        for bindings in thread_router.thread_bindings.values():
            bound_window_ids.update(bindings.values())

        stale = [
            wid
            for wid in self.window_states
            if (
                wid not in session_map_wids
                and wid not in bound_window_ids
                and wid not in live_window_ids
            )
        ]
        if not stale:
            return False
        for wid in stale:
            logger.debug("Pruning stale window_state: %s", wid)
            del self.window_states[wid]
        logger.info("Pruned %d stale window_state(s)", len(stale))
        self._save_state()
        return True

    def view_window(self, window_id: str) -> WindowView | None:
        from .window_query import view_window as _view_window

        return _view_window(window_id)

    @property
    def window_count(self) -> int:
        return len(window_store.window_states)

    def iter_window_ids(self) -> list[str]:
        return list(window_store.window_states.keys())

    def set_window_provider(
        self,
        window_id: str,
        provider_name: str,
        *,
        cwd: str | None = None,
    ) -> None:
        supports_hook = True
        if provider_name:
            from .providers.registry import UnknownProviderError, registry

            try:
                supports_hook = registry.get(provider_name).capabilities.supports_hook
            except UnknownProviderError:
                supports_hook = True
        window_store.set_window_provider(
            window_id,
            provider_name,
            cwd=cwd,
            new_provider_supports_hook=supports_hook,
        )

    def _clear_session_map_entry(self, window_id: str) -> None:
        session_map_sync.clear_session_map_entry(window_id)

    def set_window_cwd(self, window_id: str, cwd: str) -> None:
        state = window_store.get_window_state(window_id)
        state.cwd = cwd
        self._save_state()

    def set_window_origin(self, window_id: str, origin: str) -> None:
        _lifecycle_state.set_window_origin(window_id, origin)

    def set_window_worktree(
        self, window_id: str, worktree_path: str, branch: str
    ) -> None:
        _worktree_state.set_worktree(window_id, worktree_path, branch)

    def get_approval_mode(self, window_id: str) -> str:
        return _identity_state.get_approval_mode(window_id)

    def set_window_approval_mode(self, window_id: str, mode: str) -> None:
        _identity_state.set_window_approval_mode(window_id, mode.lower())

    def get_batch_mode(self, window_id: str) -> str:
        return _tool_state.get_batch_mode(window_id)

    def set_batch_mode(self, window_id: str, mode: str) -> None:
        _tool_state.set_batch_mode(window_id, mode)

    def cycle_batch_mode(self, window_id: str) -> str:
        return _tool_state.cycle_batch_mode(window_id)

    def get_tool_call_visibility(self, window_id: str) -> str:
        return _tool_state.get_tool_call_visibility(window_id)

    def set_tool_call_visibility(self, window_id: str, mode: str) -> None:
        _tool_state.set_tool_call_visibility(window_id, mode)

    def cycle_tool_call_visibility(self, window_id: str) -> str:
        return _tool_state.cycle_tool_call_visibility(window_id)


session_manager = SessionManager()
