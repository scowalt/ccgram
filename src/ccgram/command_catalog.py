import time
from dataclasses import dataclass, field
from pathlib import Path

from ccgram.providers.base import AgentProvider, DiscoveredCommand

_FrontmatterReadError = (OSError, UnicodeDecodeError)
_DEFAULT_TTL_SECONDS = 60.0


def parse_frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except _FrontmatterReadError:
        return {}

    if not text.startswith("---"):
        return {}

    end = text.find("\n---", 3)
    if end == -1:
        return {}

    result: dict[str, str] = {}
    for line in text[4:end].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip().strip("\"'")
    return result


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except OSError:
        return []


def _discover_skills(claude_dir: Path) -> list[DiscoveredCommand]:
    commands: list[DiscoveredCommand] = []
    skills_dir = claude_dir / "skills"
    if skills_dir.is_dir():
        for skill_dir in _safe_iterdir(skills_dir):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.is_file():
                continue
            fm = parse_frontmatter(skill_file)
            if fm.get("user-invocable", "").lower() != "true":
                continue
            name = fm.get("name", skill_dir.name)
            desc = fm.get("description", f"/{name}")
            commands.append(
                DiscoveredCommand(
                    name=name,
                    description=desc,
                    source="skill",
                )
            )
    return commands


def _discover_custom_commands(claude_dir: Path) -> list[DiscoveredCommand]:
    commands: list[DiscoveredCommand] = []
    commands_dir = claude_dir / "commands"
    if commands_dir.is_dir():
        for group_dir in _safe_iterdir(commands_dir):
            if not group_dir.is_dir() or group_dir.name.startswith("."):
                continue
            try:
                md_files = sorted(group_dir.glob("*.md"))
            except OSError:
                continue
            for cmd_file in md_files:
                if cmd_file.name.startswith("."):
                    continue
                name = f"{group_dir.name}:{cmd_file.stem}"
                fm = parse_frontmatter(cmd_file)
                desc = fm.get("description", f"/{name}")
                commands.append(
                    DiscoveredCommand(
                        name=name,
                        description=desc,
                        source="command",
                    )
                )
    return commands


def discover_user_defined_commands(
    claude_dir: Path | None = None,
) -> list[DiscoveredCommand]:
    if claude_dir is None:
        from ccgram.config import config

        claude_dir = config.claude_config_dir

    commands: list[DiscoveredCommand] = []
    commands.extend(_discover_skills(claude_dir))
    commands.extend(_discover_custom_commands(claude_dir))

    return commands


@dataclass(slots=True)
class CommandCatalog:
    ttl_seconds: float = _DEFAULT_TTL_SECONDS
    _cache: dict[tuple[str, str], tuple[float, list[DiscoveredCommand]]] = field(
        default_factory=dict
    )

    def get_provider_commands(
        self,
        provider: AgentProvider,
        base_dir: str,
    ) -> list[DiscoveredCommand]:
        provider_name = provider.capabilities.name
        cache_key = (provider_name, base_dir)
        now = time.monotonic()

        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self.ttl_seconds:
            return list(cached[1])

        commands = list(provider.discover_commands(base_dir))
        if provider.capabilities.supports_user_command_discovery:
            commands.extend(
                discover_user_defined_commands(Path(base_dir) if base_dir else None)
            )

        deduped: list[DiscoveredCommand] = []
        seen: set[str] = set()
        for cmd in commands:
            if not cmd.name or cmd.name in seen:
                continue
            deduped.append(cmd)
            seen.add(cmd.name)

        self._cache[cache_key] = (now, list(deduped))
        return deduped


command_catalog = CommandCatalog()
