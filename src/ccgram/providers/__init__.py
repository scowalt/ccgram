import re

import structlog
import os

from ccgram.providers.base import (
    AgentMessage,
    AgentProvider,
    DiscoveredCommand,
    ProviderCapabilities,
    SessionStartEvent,
    StatusUpdate,
)
from ccgram.providers.process_detection import JS_RUNTIMES, get_cached_foreground_pgid
from ccgram.providers.registry import ProviderRegistry, UnknownProviderError, registry

logger = structlog.get_logger()

_APPROVAL_MODE_NORMAL = "normal"
_APPROVAL_MODE_YOLO = "yolo"
_YOLO_FLAGS: dict[str, str] = {
    "claude": "--dangerously-skip-permissions",
    "codex": "--dangerously-bypass-approvals-and-sandbox",
    "gemini": "--yolo",
}


def has_yolo_mode(provider_name: str) -> bool:
    return provider_name in _YOLO_FLAGS


_active: AgentProvider | None = None


_registered = False


def _ensure_registered() -> None:
    global _registered
    if _registered:
        return
    from ccgram.providers.claude import ClaudeProvider

    from ccgram.providers.codex import CodexProvider

    from ccgram.providers.gemini import GeminiProvider

    from ccgram.providers.pi import PiProvider

    from ccgram.providers.shell import ShellProvider

    registry.register("claude", ClaudeProvider)
    registry.register("codex", CodexProvider)
    registry.register("gemini", GeminiProvider)
    registry.register("pi", PiProvider)
    registry.register("shell", ShellProvider)
    _registered = True


def get_provider() -> AgentProvider:
    global _active
    if _active is None:
        _ensure_registered()

        from ccgram.config import config

        try:
            _active = registry.get(config.provider_name)
        except UnknownProviderError:
            logger.warning(
                "Unknown provider %r, falling back to 'claude'",
                config.provider_name,
            )
            _active = registry.get("claude")
    return _active


def _reset_provider() -> None:
    global _active, _registered
    _active = None
    _registered = False


def get_provider_for_window(
    window_id: str,  # noqa: ARG001
    provider_name: str | None = None,
) -> AgentProvider:
    _ensure_registered()

    if provider_name and registry.is_valid(provider_name):
        return registry.get(provider_name)
    return get_provider()


def detect_provider_from_command(pane_current_command: str) -> str:
    cmd = pane_current_command.strip().lower()
    if not cmd:
        return ""

    basename = os.path.basename(cmd.split()[0])
    for name in ("claude", "codex", "gemini", "pi"):
        if basename == name or basename.startswith(name + "-"):
            return name

    from .shell import KNOWN_SHELLS

    if basename in KNOWN_SHELLS or basename.lstrip("-") in KNOWN_SHELLS:
        return "shell"

    return ""


_CLAUDE_PROJECTS_RE = re.compile(r"/\.claude[a-z0-9._-]*/projects/")


def detect_provider_from_transcript_path(transcript_path: str) -> str:
    if not isinstance(transcript_path, str):
        return ""
    normalized = transcript_path.strip().lower().replace("\\", "/")
    if not normalized:
        return ""
    if "/.codex/sessions/" in normalized:
        return "codex"
    if _CLAUDE_PROJECTS_RE.search(normalized):
        return "claude"
    if "/.gemini/" in normalized and "/chats/" in normalized:
        return "gemini"
    if "/.pi/agent/sessions/" in normalized:
        return "pi"
    return ""


def should_probe_pane_title_for_provider_detection(pane_current_command: str) -> bool:
    _ensure_registered()
    for name in registry.provider_names():
        if registry.get(name).requires_pane_title_for_detection(pane_current_command):
            return True
    return False


_CCGRAM_TITLE_PREFIX = "ccgram:"


def detect_provider_from_runtime(
    pane_current_command: str,
    *,
    pane_title: str = "",
) -> str:
    detected = detect_provider_from_command(pane_current_command)
    if detected or not pane_title:
        return detected

    if pane_title.startswith(_CCGRAM_TITLE_PREFIX):
        stamped = pane_title[len(_CCGRAM_TITLE_PREFIX) :].strip()
        _ensure_registered()
        if registry.is_valid(stamped):
            return stamped

    _ensure_registered()
    for name in registry.provider_names():
        provider = registry.get(name)
        if provider.detect_from_pane_title(pane_current_command, pane_title):
            return provider.capabilities.name
    return ""


async def detect_provider_from_pane(
    pane_current_command: str,
    *,
    window_id: str = "",
) -> str:
    detected = detect_provider_from_command(pane_current_command)
    if detected:
        return detected

    if not window_id:
        return ""

    cmd = pane_current_command.strip().lower()
    basename = os.path.basename(cmd.split()[0]) if cmd else ""
    if cmd and basename not in JS_RUNTIMES:
        return ""

    from .process_detection import detect_provider_cached

    from ..multiplexer import multiplexer

    fg = await multiplexer.foreground(window_id)
    return await detect_provider_cached(window_id, fg)


def resolve_launch_command(
    provider_name: str, *, approval_mode: str = _APPROVAL_MODE_NORMAL
) -> str:
    _ensure_registered()
    provider = provider_name.lower()
    env_key = f"CCGRAM_{provider.upper()}_COMMAND"
    override = os.environ.get(env_key)
    if override:
        command = override
    else:
        try:
            command = registry.get(provider).capabilities.launch_command
        except UnknownProviderError:
            provider = "claude"
            command = registry.get("claude").capabilities.launch_command

    if provider == "gemini" and not override:
        from ccgram.providers.gemini import build_hardened_gemini_launch_command

        command = build_hardened_gemini_launch_command(command)

    if approval_mode.lower() != _APPROVAL_MODE_YOLO:
        return command

    yolo_flag = _YOLO_FLAGS.get(provider)
    if not yolo_flag or yolo_flag in command:
        return command
    return f"{command} {yolo_flag}"


def resolve_capabilities(provider_name: str | None = None) -> ProviderCapabilities:
    _ensure_registered()
    name = (
        provider_name
        if provider_name is not None
        else os.environ.get("CCGRAM_PROVIDER", "claude")
    )
    try:
        return registry.get(name).capabilities
    except UnknownProviderError:
        return registry.get("claude").capabilities


__all__ = [
    "AgentMessage",
    "AgentProvider",
    "DiscoveredCommand",
    "ProviderCapabilities",
    "ProviderRegistry",
    "SessionStartEvent",
    "StatusUpdate",
    "UnknownProviderError",
    "detect_provider_from_command",
    "detect_provider_from_pane",
    "detect_provider_from_transcript_path",
    "detect_provider_from_runtime",
    "get_cached_foreground_pgid",
    "get_provider",
    "get_provider_for_window",
    "has_yolo_mode",
    "registry",
    "resolve_capabilities",
    "resolve_launch_command",
    "should_probe_pane_title_for_provider_detection",
]
