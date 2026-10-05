from __future__ import annotations

from typing import TYPE_CHECKING

from ..callback_helpers import get_thread_id
from ..user_state import (
    AWAITING_WORKTREE_BRANCH_NAME,
    PENDING_THREAD_ID,
    PENDING_THREAD_TEXT,
    PENDING_WORKSPACE_ID,
    PENDING_WORKSPACES,
    PENDING_WORKTREE_BRANCH,
    PENDING_WORKTREE_CREATING,
    PENDING_WORKTREE_DIRTY,
    PENDING_WORKTREE_PATH,
    PENDING_WORKTREE_REPO,
    PENDING_WORKTREE_SUBDIR,
)
from .directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
)

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

__all__ = [
    "AWAITING_WORKTREE_BRANCH_NAME",
    "BROWSE_DIRS_KEY",
    "BROWSE_PAGE_KEY",
    "BROWSE_PATH_KEY",
    "PENDING_THREAD_ID",
    "PENDING_THREAD_TEXT",
    "PENDING_WORKSPACE_ID",
    "PENDING_WORKSPACES",
    "PENDING_WORKTREE_BRANCH",
    "PENDING_WORKTREE_CREATING",
    "PENDING_WORKTREE_DIRTY",
    "PENDING_WORKTREE_PATH",
    "PENDING_WORKTREE_REPO",
    "PENDING_WORKTREE_SUBDIR",
    "_browser_flow_stale",
    "_required_selected_path",
]


def _browser_flow_stale(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    pending_tid = (
        context.user_data.get(PENDING_THREAD_ID) if context.user_data else None
    )
    return pending_tid is None or get_thread_id(update) != pending_tid


def _required_selected_path(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if context.user_data is None:
        return None
    path = context.user_data.get(BROWSE_PATH_KEY)
    return path if isinstance(path, str) and path else None
