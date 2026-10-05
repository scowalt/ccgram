import structlog

from ccgram.providers.base import AgentProvider

logger = structlog.get_logger()


class UnknownProviderError(LookupError):
    pass


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, type[AgentProvider]] = {}
        self._instances: dict[str, AgentProvider] = {}

    def register(self, name: str, provider_cls: type[AgentProvider]) -> None:
        self._providers[name] = provider_cls
        self._instances.pop(name, None)
        logger.debug("Registered provider %r", name)

    def provider_names(self) -> list[str]:
        return list(self._providers)

    def is_valid(self, name: str) -> bool:
        return name in self._providers

    def get(self, name: str) -> AgentProvider:
        if name in self._instances:
            return self._instances[name]
        cls = self._providers.get(name)
        if cls is None:
            available = ", ".join(sorted(self._providers)) or "(none)"
            raise UnknownProviderError(
                f"Unknown provider {name!r}. Available: {available}"
            )
        instance = cls()
        self._instances[name] = instance
        return instance


registry = ProviderRegistry()
