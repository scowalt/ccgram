from __future__ import annotations

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from ...config import config
from ...providers import get_provider_for_window
from ... import window_query
from ...topic_state_registry import topic_state
from ...toolbar_config import (
    ToolbarAction,
    ToolbarConfig,
    load_toolbar_config,
)
from ..callback_data import CB_TOOLBAR


_toolbar_cfg: ToolbarConfig | None = None


def get_toolbar_config() -> ToolbarConfig:
    global _toolbar_cfg  # noqa: PLW0603
    if _toolbar_cfg is None:
        _toolbar_cfg = load_toolbar_config(config.toolbar_config_path)
    return _toolbar_cfg


def reload_toolbar_config() -> None:
    global _toolbar_cfg  # noqa: PLW0603
    _toolbar_cfg = None


_window_action_labels: dict[str, dict[str, str]] = {}


def _set_action_label(window_id: str, action_name: str, label: str) -> None:
    _window_action_labels.setdefault(window_id, {})[action_name] = label


def _get_action_label(window_id: str, action_name: str) -> str | None:
    return _window_action_labels.get(window_id, {}).get(action_name)


@topic_state.register("window")
def _clear_toolbar_labels(window_id: str) -> None:
    _window_action_labels.pop(window_id, None)


def _make_button(
    action: ToolbarAction, window_id: str, style: str
) -> InlineKeyboardButton:
    override = _get_action_label(window_id, action.name)
    if override is not None:
        label = f"{action.emoji} {override}" if style == "emoji_text" else override
    else:
        label = action.render(style)  # type: ignore[arg-type]
    cb = f"{CB_TOOLBAR}{window_id}:{action.name}"[:64]
    return InlineKeyboardButton(label, callback_data=cb)


def build_toolbar_keyboard(
    window_id: str, provider_name: str = "claude"
) -> InlineKeyboardMarkup:
    cfg = get_toolbar_config()
    layout = cfg.for_provider(provider_name)
    rows: list[list[InlineKeyboardButton]] = []
    for row_names in layout.buttons:
        cells: list[InlineKeyboardButton] = []
        for name in row_names:
            action = cfg.actions.get(name)
            if action is not None:
                cells.append(_make_button(action, window_id, layout.style))
        if cells:
            rows.append(cells)
    return InlineKeyboardMarkup(rows)


async def seed_button_states(window_id: str) -> None:
    cfg = get_toolbar_config()
    mode_action = cfg.actions.get("mode")
    if mode_action is None or not mode_action.read_state:
        return
    provider = get_provider_for_window(
        window_id, provider_name=window_query.get_window_provider(window_id)
    )
    label = await provider.scrape_current_mode(window_id)
    if label:
        _set_action_label(window_id, "mode", label)


async def refresh_button_label(
    action: ToolbarAction,
    query: CallbackQuery,
    window_id: str,
    *,
    delay: float = 0.35,
) -> str:
    import asyncio

    import structlog

    from telegram.error import TelegramError

    logger = structlog.get_logger()

    await asyncio.sleep(delay)
    view = window_query.view_window(window_id)
    provider_name = view.provider_name if view else "claude"
    provider = get_provider_for_window(window_id, provider_name=provider_name)
    short_label = await provider.scrape_current_mode(window_id)
    if not short_label:
        short_label = "Def"
    _set_action_label(window_id, action.name, short_label)
    new_kb = build_toolbar_keyboard(window_id, provider_name)
    try:
        await query.edit_message_reply_markup(reply_markup=new_kb)
    except TelegramError as exc:
        logger.debug("Toolbar reply_markup edit failed: %s", exc)
    return short_label
