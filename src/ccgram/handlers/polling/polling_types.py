from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from ...providers.base import StatusUpdate

if TYPE_CHECKING:
    from telegram import Bot

    from ...screen_buffer import ScreenBuffer


ACTIVITY_THRESHOLD = 10.0

STARTUP_TIMEOUT = 30.0

RC_DEBOUNCE_SECONDS = 3.0

MAX_PROBE_FAILURES = 3

TYPING_INTERVAL = 4.0

PANE_COUNT_TTL = 5.0

SHELL_COMMANDS = frozenset({"bash", "zsh", "fish", "sh", "dash", "tcsh", "csh", "ksh"})


def is_shell_prompt(pane_current_command: str) -> bool:
    cmd = pane_current_command.strip().rsplit("/", 1)[-1]
    return cmd in SHELL_COMMANDS


@dataclass
class WindowPollState:
    has_seen_status: bool = False
    startup_time: float | None = None
    probe_failures: int = 0
    screen_buffer: ScreenBuffer | None = field(default=None, repr=False)
    pane_count_cache: tuple[int, float] | None = None
    unbound_timer: float | None = None
    last_pane_hash: int | None = None
    last_pyte_result: StatusUpdate | None = field(default=None, repr=False)
    last_rendered_text: str | None = None
    rc_active: bool = False
    rc_off_since: float | None = None
    last_rc_detected: bool = False


@dataclass
class TopicPollState:
    autoclose: tuple[str, float] | None = None
    last_typing_sent: float | None = None


@dataclass(frozen=True, slots=True)
class TickContext:
    window_id: str
    resolved_status_text: str | None
    is_shell_prompt: bool
    has_seen_status: bool
    is_recently_active: bool
    startup_time: float | None
    is_dead_window: bool
    supports_hook: bool


@dataclass(frozen=True, slots=True)
class TickDecision:
    send_status: bool = False
    status_text: str | None = None
    transition: Literal["idle", "done", "active", "starting"] | None = None
    show_recovery: bool = False


PaneStateName = Literal["active", "idle", "blocked", "dead"]


@dataclass(frozen=True, slots=True)
class PaneTransition:
    pane_id: str
    prev_state: PaneStateName | None
    new_state: PaneStateName
    name: str | None = None


BlockedAlertCallback = Callable[["Bot", int, str, int, str], Awaitable[None]]

PaneOutputCallback = Callable[["Bot", int, str, int, str, str], Awaitable[None]]
