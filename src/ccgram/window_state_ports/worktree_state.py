from __future__ import annotations

from dataclasses import dataclass

from ..window_state_store import window_store


@dataclass(frozen=True, slots=True)
class WorktreeProjection:
    window_id: str
    worktree_path: str | None
    worktree_branch: str | None


def get_worktree(window_id: str) -> WorktreeProjection | None:
    state = window_store.window_states.get(window_id)
    if state is None:
        return None
    return WorktreeProjection(
        window_id=window_id,
        worktree_path=state.worktree_path,
        worktree_branch=state.worktree_branch,
    )


def set_worktree(window_id: str, worktree_path: str, branch: str) -> None:
    window_store.set_worktree(window_id, worktree_path, branch)


def clear_worktree(window_id: str) -> None:
    window_store.clear_worktree(window_id)


__all__ = [
    "WorktreeProjection",
    "clear_worktree",
    "get_worktree",
    "set_worktree",
]
