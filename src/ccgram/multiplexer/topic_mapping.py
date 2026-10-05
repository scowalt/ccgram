from __future__ import annotations

from .base import MultiplexerCapabilities, WindowRef

TOPIC_PREFIX_SEPARATOR = " ▸ "


def format_agent_topic_prefix(workspace: str, tab: str) -> str:
    workspace = workspace.strip()
    tab = tab.strip()
    if workspace and tab:
        return f"{workspace}{TOPIC_PREFIX_SEPARATOR}{tab}"
    return workspace or tab


def is_agent_topic_window(window: WindowRef, caps: MultiplexerCapabilities) -> bool:
    if not caps.native_agent_status:
        return True
    return bool(window.pane_current_command.strip())
