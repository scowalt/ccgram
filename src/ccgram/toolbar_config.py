from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import structlog

logger = structlog.get_logger()

ButtonStyle = Literal["emoji", "text", "emoji_text"]
ActionType = Literal["key", "text", "builtin"]

_VALID_STYLES: frozenset[str] = frozenset({"emoji", "text", "emoji_text"})
_VALID_TYPES: frozenset[str] = frozenset({"key", "text", "builtin"})

_MAX_NAME_LEN = 24

_MAX_ROW_WIDTH = 8


@dataclass(frozen=True, slots=True)
class ToolbarAction:
    name: str
    """Stable identifier — used in TOML config and in callback_data."""
    emoji: str
    """Single-glyph emoji (or short symbol like '^D')."""
    text: str
    """Short text label (≤6 chars to fit Telegram's per-cell budget)."""
    action_type: ActionType
    payload: str
    """Type-dependent payload. See class docstring."""
    literal: bool = False
    """For ``key`` actions: whether the key string is literal vs. a named key."""
    read_state: bool = False
    """For ``key``/``text`` actions: capture pane after sending and surface
    the most recent mode-line in the toast (used by Mode/Think/YOLO)."""

    def render(self, style: ButtonStyle) -> str:
        if style == "emoji":
            return self.emoji
        if style == "text":
            return self.text
        return f"{self.emoji} {self.text}"


@dataclass(frozen=True, slots=True)
class ToolbarLayout:
    style: ButtonStyle
    buttons: tuple[tuple[str, ...], ...]
    """Grid of action names: outer = rows, inner = cells per row."""


def _b(
    name: str,
    emoji: str,
    text: str,
    action_type: ActionType,
    payload: str,
    *,
    literal: bool = False,
    read_state: bool = False,
) -> ToolbarAction:
    return ToolbarAction(
        name=name,
        emoji=emoji,
        text=text,
        action_type=action_type,
        payload=payload,
        literal=literal,
        read_state=read_state,
    )


BUILTIN_ACTIONS: dict[str, ToolbarAction] = {
    a.name: a
    for a in (
        _b("screen", "\U0001f4f7", "Screen", "builtin", "screenshot"),
        _b("ctrlc", "\u23f9", "Ctrl-C", "builtin", "ctrlc"),
        _b("live", "\U0001f4fa", "Live", "builtin", "live"),
        _b("getfile", "\U0001f4e5", "Get File", "builtin", "getfile"),
        _b("last", "\U0001f4c4", "Last", "builtin", "lastreply"),
        _b("close", "\u2716", "Close", "builtin", "dismiss"),
        _b(
            "mode",
            "\U0001f500",
            "Mode",
            "key",
            "\x1b[Z",
            literal=True,
            read_state=True,
        ),
        _b("think", "\U0001f4ad", "Think", "key", "M-t"),
        _b("yolo", "\U0001f1fe", "YOLO", "key", "C-y", read_state=True),
        _b("esc", "\u238b", "Esc", "key", "Escape"),
        _b("enter", "\u23ce", "Enter", "key", "Enter"),
        _b("tab", "\u21e5", "Tab", "key", "Tab"),
        _b("eof", "^D", "EOF", "key", "C-d"),
        _b("susp", "^Z", "Susp", "key", "C-z"),
        _b("up", "\U0001f53c", "Up", "key", "Up"),
        _b("down", "\U0001f53d", "Down", "key", "Down"),
        _b("model", "π", "Model", "text", "/model"),
    )
}


DEFAULT_LAYOUTS: dict[str, ToolbarLayout] = {
    "claude": ToolbarLayout(
        style="emoji_text",
        buttons=(
            ("screen", "ctrlc", "live"),
            ("mode", "think", "esc"),
            ("up", "enter", "down"),
            ("last", "getfile", "close"),
        ),
    ),
    "codex": ToolbarLayout(
        style="emoji_text",
        buttons=(
            ("screen", "ctrlc", "live"),
            ("esc", "tab", "mode"),
            ("up", "enter", "down"),
            ("last", "getfile", "close"),
        ),
    ),
    "gemini": ToolbarLayout(
        style="emoji_text",
        buttons=(
            ("screen", "ctrlc", "live"),
            ("mode", "yolo", "esc"),
            ("up", "enter", "down"),
            ("last", "getfile", "close"),
        ),
    ),
    "pi": ToolbarLayout(
        style="emoji_text",
        buttons=(
            ("screen", "ctrlc", "live"),
            ("esc", "tab", "model"),
            ("up", "enter", "down"),
            ("last", "getfile", "close"),
        ),
    ),
    "shell": ToolbarLayout(
        style="emoji_text",
        buttons=(
            ("screen", "ctrlc", "live"),
            ("enter", "eof", "susp"),
            ("last", "getfile", "esc", "close"),
        ),
    ),
}


@dataclass(slots=True)
class ToolbarConfig:
    layouts: dict[str, ToolbarLayout] = field(default_factory=dict)
    actions: dict[str, ToolbarAction] = field(default_factory=dict)

    def for_provider(self, provider_name: str) -> ToolbarLayout:
        return self.layouts.get(provider_name) or self.layouts["claude"]


def _parse_action(name: str, raw: object) -> ToolbarAction | None:
    if not isinstance(name, str) or not name:
        logger.warning("Toolbar config: action name must be a non-empty string")
        return None
    if len(name) > _MAX_NAME_LEN:
        logger.warning(
            "Toolbar config: action %r exceeds %d chars (callback_data overflow risk), ignoring",
            name,
            _MAX_NAME_LEN,
        )
        return None
    if not isinstance(raw, dict):
        logger.warning("Toolbar config: action %r is not a table, ignoring", name)
        return None
    emoji = str(raw.get("emoji", "")).strip()
    text = str(raw.get("text", "")).strip()
    action_type = str(raw.get("type", "")).strip()
    payload = str(raw.get("payload", "")).strip()
    if not (emoji or text):
        logger.warning("Toolbar config: action %r needs emoji or text, ignoring", name)
        return None
    if action_type not in _VALID_TYPES:
        logger.warning(
            "Toolbar config: action %r type=%r invalid (use one of %s), ignoring",
            name,
            action_type,
            sorted(_VALID_TYPES),
        )
        return None
    if action_type == "builtin":
        logger.warning(
            "Toolbar config: action %r type=builtin is reserved for built-ins; "
            "use type=key or type=text instead",
            name,
        )
        return None
    if not payload:
        logger.warning("Toolbar config: action %r missing payload, ignoring", name)
        return None
    return ToolbarAction(
        name=name,
        emoji=emoji or text,
        text=text or name,
        action_type=action_type,  # type: ignore[arg-type]
        payload=payload,
        literal=bool(raw.get("literal", False)),
        read_state=bool(raw.get("read_state", False)),
    )


def _parse_style(provider: str, raw_style: object) -> ButtonStyle:
    style = str(raw_style)
    if style not in _VALID_STYLES:
        logger.warning(
            "Toolbar config: %s.style=%r invalid (use one of %s), defaulting to emoji_text",
            provider,
            style,
            sorted(_VALID_STYLES),
        )
        return "emoji_text"
    return style  # type: ignore[return-value]


def _parse_row(
    provider: str,
    row_idx: int,
    row: object,
    action_pool: dict[str, ToolbarAction],
) -> tuple[str, ...]:
    if not isinstance(row, list):
        logger.warning(
            "Toolbar config: %s.buttons[%d] is not a list, skipping",
            provider,
            row_idx,
        )
        return ()
    cells: list[str] = []
    for name in row:
        if isinstance(name, str) and name in action_pool:
            cells.append(name)
        else:
            logger.warning(
                "Toolbar config: unknown action %r in %s row %d, skipping cell",
                name,
                provider,
                row_idx,
            )
    if len(cells) > _MAX_ROW_WIDTH:
        logger.warning(
            "Toolbar config: %s row %d has %d cells (max %d), trimming",
            provider,
            row_idx,
            len(cells),
            _MAX_ROW_WIDTH,
        )
        cells = cells[:_MAX_ROW_WIDTH]
    return tuple(cells)


def _parse_layout(
    provider: str, raw: object, action_pool: dict[str, ToolbarAction]
) -> ToolbarLayout | None:
    if not isinstance(raw, dict):
        logger.warning("Toolbar config: provider %r is not a table, ignoring", provider)
        return None
    buttons_raw = raw.get("buttons")
    if not isinstance(buttons_raw, list) or not buttons_raw:
        logger.warning(
            "Toolbar config: %s.buttons missing or empty, ignoring", provider
        )
        return None
    style = _parse_style(provider, raw.get("style", "emoji_text"))
    rows = [
        cells
        for row_idx, row in enumerate(buttons_raw)
        if (cells := _parse_row(provider, row_idx, row, action_pool))
    ]
    if not rows:
        logger.warning("Toolbar config: %s has no usable rows, ignoring", provider)
        return None
    return ToolbarLayout(style=style, buttons=tuple(rows))


def _read_toml(path: Path) -> dict | None:
    if not path.exists():
        logger.debug("Toolbar config file not found: %s — using defaults", path)
        return None
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("Toolbar config %s unreadable (%s) — using defaults", path, e)
        return None
    if not isinstance(raw, dict):
        logger.warning("Toolbar config %s root must be a table — using defaults", path)
        return None
    return raw


def _apply_user_actions(cfg: ToolbarConfig, raw: dict) -> None:
    user_actions = raw.get("actions") or {}
    if not isinstance(user_actions, dict):
        return
    for name, raw_action in user_actions.items():
        action = _parse_action(name, raw_action)
        if action is not None:
            cfg.actions[name] = action


def _apply_user_layouts(cfg: ToolbarConfig, raw: dict) -> None:
    user_providers = raw.get("providers") or {}
    if not isinstance(user_providers, dict):
        return
    for provider, raw_layout in user_providers.items():
        layout = _parse_layout(provider, raw_layout, cfg.actions)
        if layout is not None:
            cfg.layouts[provider] = layout


def load_toolbar_config(path: str | Path | None = None) -> ToolbarConfig:
    cfg = ToolbarConfig(
        layouts=dict(DEFAULT_LAYOUTS),
        actions=dict(BUILTIN_ACTIONS),
    )
    if not path:
        return cfg
    raw = _read_toml(Path(path).expanduser())
    if raw is None:
        return cfg
    _apply_user_actions(cfg, raw)
    _apply_user_layouts(cfg, raw)
    return cfg
