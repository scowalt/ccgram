import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, cast

from ccgram.command_catalog import (
    command_catalog,
    discover_user_defined_commands,
    parse_frontmatter as _parse_frontmatter,
)
from ccgram.providers.base import AgentProvider
from telegram import Bot, BotCommand, BotCommandScope

logger = structlog.get_logger()

CC_BUILTINS: dict[str, str] = {
    "agents": "↗ Manage subagent configurations",
    "background": "↗ Detach session as background agent",
    "branch": "↗ Fork conversation at current point",
    "clear": "↗ Clear conversation context",
    "compact": "↗ Summarize history to free context",
    "context": "↗ Show context window usage",
    "copy": "↗ Copy last response to clipboard",
    "cost": "↗ Show session cost (alias /usage)",
    "diff": "↗ Open interactive diff viewer",
    "doctor": "↗ Diagnose Claude Code installation",
    "effort": "↗ Adjust thinking effort level",
    "export": "↗ Export conversation as text",
    "feedback": "↗ Submit feedback or bug report",
    "help": "↗ Show Claude Code help",
    "init": "↗ Initialize CLAUDE.md in project",
    "loop": "↗ Run prompt repeatedly on a schedule",
    "mcp": "↗ Manage MCP server connections",
    "memory": "↗ Edit CLAUDE.md memory files",
    "model": "↗ Select model and effort",
    "permissions": "↗ Manage tool permissions",
    "plan": "↗ Enter plan mode",
    "rc": "↗ Start remote control (alias)",
    "recap": "↗ Summarize current session",
    "release-notes": "↗ View changelog",
    "remote-control": "↗ Start remote control session",
    "review": "↗ Review pull request locally",
    "rewind": "↗ Rewind conversation or code",
    "security-review": "↗ Analyze pending changes for security",
    "settings": "↗ Open Settings interface",
    "skills": "↗ List available skills",
    "statusline": "↗ Configure status line",
    "status": "↗ Show session status",
    "tasks": "↗ Manage background tasks",
    "theme": "↗ Change color theme (picker)",
    "tui": "↗ Switch terminal UI renderer",
    "usage": "↗ Show usage and cost stats",
    "verify": "↗ Build and run app to verify change",
}

_BOT_COMMANDS: list[tuple[str, str]] = [
    ("start", "Show ccgram welcome message"),
    ("commands", "List commands for this topic provider"),
    ("history", "Message history for this topic"),
    ("sessions", "Sessions dashboard"),
    ("resume", "Browse and resume past sessions"),
    ("screenshot", "Capture terminal screenshot"),
    ("live", "Open auto-refreshing terminal view"),
    ("panes", "List panes in this window"),
    ("restore", "Recover a dead topic"),
    ("sync", "Audit and fix state"),
    ("unbind", "Unbind this topic"),
    ("recall", "Recall recent commands"),
    ("send", "Send a workspace file to Telegram"),
    ("toolbar", "Show action toolbar"),
    ("verbose", "Toggle tool call batching"),
    ("upgrade", "Upgrade ccgram and restart"),
]

_MAX_TELEGRAM_COMMANDS = 100
_MAX_DESCRIPTION_LEN = 256


@dataclass(frozen=True, slots=True)
class CCCommand:
    name: str
    telegram_name: str
    description: str
    source: Literal["builtin", "skill", "command"]


def _sanitize_telegram_name(name: str) -> str:
    sanitized = name.lower().replace("-", "_").replace(":", "_")
    sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
    return sanitized[:32]


def _cc_desc(desc: str) -> str:
    return desc if desc.startswith("↗") else f"↗ {desc}"


def parse_frontmatter(path: Path) -> dict[str, str]:
    return _parse_frontmatter(path)


def discover_cc_commands(claude_dir: Path | None = None) -> list[CCCommand]:
    if claude_dir is None:
        from ccgram.config import config

        claude_dir = config.claude_config_dir

    commands: list[CCCommand] = []

    for name, desc in CC_BUILTINS.items():
        commands.append(
            CCCommand(
                name=name,
                telegram_name=_sanitize_telegram_name(name),
                description=desc,
                source="builtin",
            )
        )

    for cmd in discover_user_defined_commands(claude_dir):
        tg_name = _sanitize_telegram_name(cmd.name)
        if not tg_name:
            continue
        commands.append(
            CCCommand(
                name=cmd.name,
                telegram_name=tg_name,
                description=_cc_desc(cmd.description),
                source=cast(Literal["builtin", "skill", "command"], cmd.source),
            )
        )

    return commands


_name_map: dict[str, str] = {}


def _provider_base_dir(claude_dir: Path | None = None) -> str:
    from ccgram.config import config as _cfg

    return str(claude_dir) if claude_dir else str(_cfg.claude_config_dir)


def discover_provider_commands(
    provider: AgentProvider,
    claude_dir: Path | None = None,
) -> list[CCCommand]:
    base_dir = _provider_base_dir(claude_dir)
    valid_sources = {"builtin", "skill", "command"}
    discovered = command_catalog.get_provider_commands(provider, base_dir)
    commands: list[CCCommand] = []
    for cmd in discovered:
        if not cmd.name:
            continue
        commands.append(
            CCCommand(
                name=cmd.name,
                telegram_name=_sanitize_telegram_name(cmd.name),
                description=_cc_desc(cmd.description),
                source=cast(
                    Literal["builtin", "skill", "command"],
                    cmd.source if cmd.source in valid_sources else "command",
                ),
            )
        )
    return commands


def get_provider_command_map(
    provider: AgentProvider,
    claude_dir: Path | None = None,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cmd in discover_provider_commands(provider, claude_dir):
        if cmd.telegram_name and cmd.telegram_name not in mapping:
            mapping[cmd.telegram_name] = cmd.name
    return mapping


def get_provider_supported_commands(
    provider: AgentProvider,
    claude_dir: Path | None = None,
) -> set[str]:
    supported: set[str] = set()
    for name in get_provider_command_map(provider, claude_dir).values():
        token = name if name.startswith("/") else f"/{name}"
        supported.add(token.lower())
    for name in provider.capabilities.builtin_commands:
        if not name:
            continue
        token = name if name.startswith("/") else f"/{name}"
        supported.add(token.lower())
    return supported


def _refresh_cache(
    claude_dir: Path | None = None,
    provider: AgentProvider | None = None,
    providers: Iterable[AgentProvider] | None = None,
) -> list[CCCommand]:
    global _name_map

    if providers is not None:
        commands = []
        for discovered_provider in providers:
            commands.extend(discover_provider_commands(discovered_provider, claude_dir))
    elif provider is not None:
        commands = discover_provider_commands(provider, claude_dir)
    else:
        commands = discover_cc_commands(claude_dir)
    new_map: dict[str, str] = {}
    for cmd in commands:
        if cmd.telegram_name not in new_map:
            new_map[cmd.telegram_name] = cmd.name
    _name_map = new_map
    return commands


def get_cc_name(telegram_name: str) -> str | None:
    return _name_map.get(telegram_name)


async def register_commands(
    bot: Bot,
    claude_dir: Path | None = None,
    provider: AgentProvider | None = None,
    providers: Iterable[AgentProvider] | None = None,
    include_cc_commands: bool = True,
    scope: BotCommandScope | None = None,
) -> None:
    commands = (
        _refresh_cache(claude_dir, provider=provider, providers=providers)
        if include_cc_commands
        else []
    )

    bot_commands = [BotCommand(name, desc) for name, desc in _BOT_COMMANDS]
    max_cc = _MAX_TELEGRAM_COMMANDS - len(bot_commands)

    seen_names: set[str] = {name for name, _ in _BOT_COMMANDS}
    cc_count = 0
    for cmd in commands:
        if cc_count >= max_cc:
            break
        if not cmd.telegram_name or cmd.telegram_name in seen_names:
            continue
        seen_names.add(cmd.telegram_name)
        desc = cmd.description[:_MAX_DESCRIPTION_LEN]
        bot_commands.append(BotCommand(cmd.telegram_name, desc))
        cc_count += 1

    import asyncio

    from telegram.error import RetryAfter

    max_startup_wait = 120

    try:
        if scope is None:
            await bot.delete_my_commands()
            await bot.set_my_commands(bot_commands)
        else:
            await bot.delete_my_commands(scope=scope)
            await bot.set_my_commands(bot_commands, scope=scope)
    except RetryAfter as e:
        retry_secs = (
            e.retry_after
            if isinstance(e.retry_after, int)
            else int(e.retry_after.total_seconds())
        )
        if retry_secs > max_startup_wait:
            logger.warning(
                "Telegram flood control: %ds cooldown, skipping command registration"
                " (will retry in 10min refresh cycle)",
                retry_secs,
            )
            return
        logger.warning(
            "Telegram flood control: retrying command registration in %ds",
            retry_secs,
        )
        await asyncio.sleep(retry_secs)
        if scope is None:
            await bot.delete_my_commands()
            await bot.set_my_commands(bot_commands)
        else:
            await bot.delete_my_commands(scope=scope)
            await bot.set_my_commands(bot_commands, scope=scope)
    logger.info("Registered %d bot commands (%d CC)", len(bot_commands), cc_count)
