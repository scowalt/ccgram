from dataclasses import dataclass
from typing import Protocol


@dataclass
class CommandResult:
    command: str
    explanation: str
    is_dangerous: bool = False


class TextCompleter(Protocol):
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str: ...


class CommandGenerator(Protocol):
    async def generate_command(
        self,
        description: str,
        *,
        cwd: str = "",
        shell: str = "",
        os_info: str = "",
        recent_output: str = "",
        shell_tools: str = "",
    ) -> CommandResult: ...
