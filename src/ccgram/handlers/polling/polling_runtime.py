from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .polling_state import (
        InteractiveUIStrategy,
        PaneStatusStrategy,
        TerminalPollState,
        TerminalScreenBuffer,
        TopicLifecycleStrategy,
    )


@dataclass
class PollingRuntime:
    poll_state: "TerminalPollState"
    screen_buffer: "TerminalScreenBuffer"
    interactive: "InteractiveUIStrategy"
    lifecycle: "TopicLifecycleStrategy"
    pane_status: "PaneStatusStrategy"

    @classmethod
    def create(cls) -> "PollingRuntime":
        from .polling_state import (
            InteractiveUIStrategy,
            PaneStatusStrategy,
            TerminalPollState,
            TerminalScreenBuffer,
            TopicLifecycleStrategy,
        )

        poll_state = TerminalPollState()
        screen_buffer = TerminalScreenBuffer(poll_state)
        interactive = InteractiveUIStrategy()
        lifecycle = TopicLifecycleStrategy(poll_state)
        pane_status = PaneStatusStrategy(screen_buffer, interactive)
        return cls(
            poll_state=poll_state,
            screen_buffer=screen_buffer,
            interactive=interactive,
            lifecycle=lifecycle,
            pane_status=pane_status,
        )

    def reset_window(self, window_id: str) -> None:
        self.poll_state.clear_seen_status(window_id)
        self.screen_buffer.clear_screen_buffer(window_id)


def _build_default_runtime() -> "PollingRuntime":
    from .polling_state import (
        interactive_strategy,
        lifecycle_strategy,
        pane_status_strategy,
        terminal_poll_state,
        terminal_screen_buffer,
    )

    return PollingRuntime(
        poll_state=terminal_poll_state,
        screen_buffer=terminal_screen_buffer,
        interactive=interactive_strategy,
        lifecycle=lifecycle_strategy,
        pane_status=pane_status_strategy,
    )


_default_runtime: PollingRuntime | None = None


def get_default_runtime() -> PollingRuntime:
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = _build_default_runtime()
    return _default_runtime
