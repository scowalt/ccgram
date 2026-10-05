from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class WindowRef:
    window_id: str
    window_name: str
    cwd: str
    pane_current_command: str = ""
    pane_tty: str = ""
    pane_width: int = 0
    pane_height: int = 0


@dataclass
class PaneInfo:
    pane_id: str
    index: int
    active: bool
    command: str
    path: str
    width: int
    height: int


@dataclass
class CaptureResult:
    text: str
    truncated: bool = False


@dataclass
class ForegroundInfo:
    pid: int
    pgid: int
    argv: list[str]
    cwd: str
    tty: str = ""


@dataclass
class PaneDims:
    width: int
    height: int


@dataclass
class WorkspaceRef:
    workspace_id: str
    label: str
    cwd: str


@dataclass(frozen=True)
class AgentStatus:
    state: str
    agent: str = ""
    custom_status: str = ""


@dataclass(frozen=True)
class MuxEvent:
    kind: str
    window_id: str
    pane_id: str = ""
    status: AgentStatus | None = None


@dataclass(frozen=True)
class MultiplexerCapabilities:
    name: str
    """Backend identifier — for logging and doctor only.  Not for conditionals."""

    ids_stable_across_restart: bool
    """True when window IDs survive a server restart (tmux: True; herdr: False)."""

    exposes_pane_tty: bool
    """True when ``foreground()`` can return a tty device path (tmux: True)."""

    native_agent_status: bool
    """True when the backend exposes agent status natively (herdr: True)."""

    read_max_lines: int | None
    """Maximum scrollback lines the backend can return; None = unlimited (tmux)."""

    self_identify_env: str
    """Environment variable set by the backend for hook identity resolution."""

    supports_event_stream: bool
    """True when the backend has a push event stream (herdr: True, tmux: False).

    Gates ``watch_events`` consumption: ``bootstrap.start_event_stream`` only
    starts the ``EventStreamMonitor`` on backends with this flag (herdr); tmux's
    ``watch_events`` is an empty async generator that is never consumed.
    """

    native_worktrees: bool
    """True when the backend creates git worktrees natively (herdr: True).

    Gates the ``/new`` worktree step onto ``create_worktree_window``: herdr makes
    the checkout and groups it under the parent repo in its workspace UI. tmux
    (False) keeps ccgram's own ``git worktree add`` + ``create_window`` path.
    """


@runtime_checkable
class Multiplexer(Protocol):
    @property
    def capabilities(self) -> MultiplexerCapabilities: ...

    async def ensure_session(self) -> None: ...

    async def list_windows(self) -> list[WindowRef]: ...

    async def list_workspaces(self) -> list[WorkspaceRef]: ...

    async def capture_scrollback(
        self, window_id: str, lines: int = 200
    ) -> CaptureResult | None: ...

    async def pane_dims(self, window_id: str) -> PaneDims | None: ...

    async def send(
        self,
        window_id: str,
        text: str,
        *,
        enter: bool = True,
        literal: bool = True,
        raw: bool = False,
    ) -> bool: ...

    async def send_to_pane(
        self,
        pane_id: str,
        text: str,
        *,
        enter: bool = True,
        literal: bool = True,
        window_id: str | None = None,
    ) -> bool: ...

    async def kill_window(self, window_id: str) -> bool: ...

    async def rename_window(self, window_id: str, new_name: str) -> bool: ...

    async def list_panes(self, window_id: str) -> list[PaneInfo]: ...

    async def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        start_agent: bool = True,
        agent_args: str = "",
        launch_command: str | None = None,
        *,
        workspace_id: str | None = None,
    ) -> tuple[bool, str, str, str]: ...

    async def create_worktree_window(
        self,
        repo_path: str,
        worktree_path: str,
        branch: str,
        *,
        window_name: str | None = None,
        launch_command: str | None = None,
    ) -> tuple[bool, str, str, str]: ...

    async def foreground(self, window_id: str) -> ForegroundInfo | None: ...

    async def agent_status(self, window_id: str) -> "AgentStatus | None": ...

    async def split_window(self, window_id: str) -> str | None: ...

    def watch_events(
        self, window_ids: Sequence[str]
    ) -> AsyncGenerator[MuxEvent, None]: ...

    async def find_window_by_id(self, window_id: str) -> WindowRef | None: ...

    async def capture_pane(
        self, window_id: str, with_ansi: bool = False
    ) -> str | None: ...

    async def capture_pane_by_id(
        self,
        pane_id: str,
        *,
        with_ansi: bool = False,
        window_id: str | None = None,
    ) -> str | None: ...

    async def capture_pane_scrollback(
        self, window_id: str, history: int = 200
    ) -> str | None: ...

    async def send_keys(
        self,
        window_id: str,
        text: str,
        enter: bool = True,
        literal: bool = True,
        *,
        raw: bool = False,
    ) -> bool: ...

    async def send_keys_to_pane(
        self,
        pane_id: str,
        text: str,
        *,
        enter: bool = True,
        literal: bool = True,
        window_id: str | None = None,
    ) -> bool: ...

    async def get_pane_title(self, window_id: str) -> str: ...

    async def stamp_pane_title(self, window_id: str, provider_name: str) -> None: ...
