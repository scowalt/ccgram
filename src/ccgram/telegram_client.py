from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

from telegram import Bot, BotCommand, ChatFullInfo, File, ForumTopic, Message
from telegram._botcommandscope import BotCommandScope
from telegram._files.inputmedia import InputMedia
from telegram._reaction import ReactionType


@runtime_checkable
class TelegramClient(Protocol):
    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        **kwargs: Any,
    ) -> Message: ...

    async def edit_message_text(
        self,
        text: str,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool: ...

    async def edit_message_media(
        self,
        media: InputMedia,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool: ...

    async def edit_message_caption(
        self,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool: ...

    async def delete_message(
        self,
        chat_id: int | str,
        message_id: int,
        **kwargs: Any,
    ) -> bool: ...

    async def send_photo(
        self,
        chat_id: int | str,
        photo: Any,
        **kwargs: Any,
    ) -> Message: ...

    async def send_document(
        self,
        chat_id: int | str,
        document: Any,
        **kwargs: Any,
    ) -> Message: ...

    async def send_chat_action(
        self,
        chat_id: int | str,
        action: str,
        **kwargs: Any,
    ) -> bool: ...

    async def send_voice(
        self,
        chat_id: int | str,
        voice: Any,
        **kwargs: Any,
    ) -> Message: ...

    async def set_message_reaction(
        self,
        chat_id: int | str,
        message_id: int,
        reaction: Sequence[ReactionType | str] | ReactionType | str | None = None,
        **kwargs: Any,
    ) -> bool: ...

    async def get_chat(self, chat_id: int | str, **kwargs: Any) -> ChatFullInfo: ...

    async def get_file(self, file_id: str, **kwargs: Any) -> File: ...

    async def create_forum_topic(
        self,
        chat_id: int | str,
        name: str,
        **kwargs: Any,
    ) -> ForumTopic: ...

    async def edit_forum_topic(
        self,
        chat_id: int | str,
        message_thread_id: int,
        **kwargs: Any,
    ) -> bool: ...

    async def close_forum_topic(
        self,
        chat_id: int | str,
        message_thread_id: int,
        **kwargs: Any,
    ) -> bool: ...

    async def delete_forum_topic(
        self,
        chat_id: int | str,
        message_thread_id: int,
        **kwargs: Any,
    ) -> bool: ...

    async def unpin_all_forum_topic_messages(
        self,
        chat_id: int | str,
        message_thread_id: int,
        **kwargs: Any,
    ) -> bool: ...

    async def delete_my_commands(
        self,
        scope: BotCommandScope | None = None,
        language_code: str | None = None,
        **kwargs: Any,
    ) -> bool: ...

    async def set_my_commands(
        self,
        commands: Sequence[BotCommand | tuple[str, str]],
        scope: BotCommandScope | None = None,
        language_code: str | None = None,
        **kwargs: Any,
    ) -> bool: ...


class PTBTelegramClient:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    @property
    def bot(self) -> Bot:
        return self._bot

    async def send_message(
        self, chat_id: int | str, text: str, **kwargs: Any
    ) -> Message:
        return await self._bot.send_message(chat_id=chat_id, text=text, **kwargs)

    async def edit_message_text(
        self,
        text: str,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return await self._bot.edit_message_text(
            text=text, chat_id=chat_id, message_id=message_id, **kwargs
        )

    async def edit_message_media(
        self,
        media: InputMedia,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return await self._bot.edit_message_media(
            media=media, chat_id=chat_id, message_id=message_id, **kwargs
        )

    async def edit_message_caption(
        self,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return await self._bot.edit_message_caption(
            chat_id=chat_id, message_id=message_id, **kwargs
        )

    async def delete_message(
        self, chat_id: int | str, message_id: int, **kwargs: Any
    ) -> bool:
        return await self._bot.delete_message(
            chat_id=chat_id, message_id=message_id, **kwargs
        )

    async def send_photo(
        self, chat_id: int | str, photo: Any, **kwargs: Any
    ) -> Message:
        return await self._bot.send_photo(chat_id=chat_id, photo=photo, **kwargs)

    async def send_document(
        self, chat_id: int | str, document: Any, **kwargs: Any
    ) -> Message:
        return await self._bot.send_document(
            chat_id=chat_id, document=document, **kwargs
        )

    async def send_chat_action(
        self, chat_id: int | str, action: str, **kwargs: Any
    ) -> bool:
        return await self._bot.send_chat_action(
            chat_id=chat_id, action=action, **kwargs
        )

    async def send_voice(
        self, chat_id: int | str, voice: Any, **kwargs: Any
    ) -> Message:
        return await self._bot.send_voice(chat_id=chat_id, voice=voice, **kwargs)

    async def set_message_reaction(
        self,
        chat_id: int | str,
        message_id: int,
        reaction: Sequence[ReactionType | str] | ReactionType | str | None = None,
        **kwargs: Any,
    ) -> bool:
        return await self._bot.set_message_reaction(
            chat_id=chat_id, message_id=message_id, reaction=reaction, **kwargs
        )

    async def get_chat(self, chat_id: int | str, **kwargs: Any) -> ChatFullInfo:
        return await self._bot.get_chat(chat_id=chat_id, **kwargs)

    async def get_file(self, file_id: str, **kwargs: Any) -> File:
        return await self._bot.get_file(file_id=file_id, **kwargs)

    async def create_forum_topic(
        self, chat_id: int | str, name: str, **kwargs: Any
    ) -> ForumTopic:
        return await self._bot.create_forum_topic(chat_id=chat_id, name=name, **kwargs)

    async def edit_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return await self._bot.edit_forum_topic(
            chat_id=chat_id, message_thread_id=message_thread_id, **kwargs
        )

    async def close_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return await self._bot.close_forum_topic(
            chat_id=chat_id, message_thread_id=message_thread_id, **kwargs
        )

    async def delete_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return await self._bot.delete_forum_topic(
            chat_id=chat_id, message_thread_id=message_thread_id, **kwargs
        )

    async def unpin_all_forum_topic_messages(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return await self._bot.unpin_all_forum_topic_messages(
            chat_id=chat_id, message_thread_id=message_thread_id, **kwargs
        )

    async def delete_my_commands(
        self,
        scope: BotCommandScope | None = None,
        language_code: str | None = None,
        **kwargs: Any,
    ) -> bool:
        return await self._bot.delete_my_commands(
            scope=scope, language_code=language_code, **kwargs
        )

    async def set_my_commands(
        self,
        commands: Sequence[BotCommand | tuple[str, str]],
        scope: BotCommandScope | None = None,
        language_code: str | None = None,
        **kwargs: Any,
    ) -> bool:
        return await self._bot.set_my_commands(
            commands=commands, scope=scope, language_code=language_code, **kwargs
        )


@dataclass
class _FakeCall:
    method: str
    kwargs: dict[str, Any]


@dataclass
class FakeTelegramClient:
    calls: list[_FakeCall] = field(default_factory=list)
    returns: dict[str, Any] = field(default_factory=dict)

    def _record(self, method: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append(_FakeCall(method=method, kwargs=dict(kwargs)))
        if method in self.returns:
            spec = self.returns[method]
            if inspect.isfunction(spec) or inspect.ismethod(spec):
                return spec(**kwargs)
            return spec
        return _DEFAULT_RETURNS.get(method, True)

    def call_count(self, method: str) -> int:
        return sum(1 for c in self.calls if c.method == method)

    def last_call(self, method: str) -> _FakeCall | None:
        for call in reversed(self.calls):
            if call.method == method:
                return call
        return None

    def set_side_effect(self, method: str, effects: Sequence[Any]) -> None:
        iterator = iter(effects)

        def step(**_kwargs: Any) -> Any:
            value = next(iterator)
            if isinstance(value, BaseException):
                raise value
            return value

        self.returns[method] = step

    async def send_message(
        self, chat_id: int | str, text: str, **kwargs: Any
    ) -> Message:
        return self._record(
            "send_message", {"chat_id": chat_id, "text": text, **kwargs}
        )

    async def edit_message_text(
        self,
        text: str,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return self._record(
            "edit_message_text",
            {"text": text, "chat_id": chat_id, "message_id": message_id, **kwargs},
        )

    async def edit_message_media(
        self,
        media: InputMedia,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return self._record(
            "edit_message_media",
            {"media": media, "chat_id": chat_id, "message_id": message_id, **kwargs},
        )

    async def edit_message_caption(
        self,
        chat_id: int | str | None = None,
        message_id: int | None = None,
        **kwargs: Any,
    ) -> Message | bool:
        return self._record(
            "edit_message_caption",
            {"chat_id": chat_id, "message_id": message_id, **kwargs},
        )

    async def delete_message(
        self, chat_id: int | str, message_id: int, **kwargs: Any
    ) -> bool:
        return self._record(
            "delete_message",
            {"chat_id": chat_id, "message_id": message_id, **kwargs},
        )

    async def send_photo(
        self, chat_id: int | str, photo: Any, **kwargs: Any
    ) -> Message:
        return self._record(
            "send_photo", {"chat_id": chat_id, "photo": photo, **kwargs}
        )

    async def send_document(
        self, chat_id: int | str, document: Any, **kwargs: Any
    ) -> Message:
        return self._record(
            "send_document", {"chat_id": chat_id, "document": document, **kwargs}
        )

    async def send_chat_action(
        self, chat_id: int | str, action: str, **kwargs: Any
    ) -> bool:
        return self._record(
            "send_chat_action", {"chat_id": chat_id, "action": action, **kwargs}
        )

    async def send_voice(
        self, chat_id: int | str, voice: Any, **kwargs: Any
    ) -> Message:
        return self._record(
            "send_voice", {"chat_id": chat_id, "voice": voice, **kwargs}
        )

    async def set_message_reaction(
        self,
        chat_id: int | str,
        message_id: int,
        reaction: Sequence[ReactionType | str] | ReactionType | str | None = None,
        **kwargs: Any,
    ) -> bool:
        return self._record(
            "set_message_reaction",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reaction": reaction,
                **kwargs,
            },
        )

    async def get_chat(self, chat_id: int | str, **kwargs: Any) -> ChatFullInfo:
        return self._record("get_chat", {"chat_id": chat_id, **kwargs})

    async def get_file(self, file_id: str, **kwargs: Any) -> File:
        return self._record("get_file", {"file_id": file_id, **kwargs})

    async def create_forum_topic(
        self, chat_id: int | str, name: str, **kwargs: Any
    ) -> ForumTopic:
        return self._record(
            "create_forum_topic", {"chat_id": chat_id, "name": name, **kwargs}
        )

    async def edit_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return self._record(
            "edit_forum_topic",
            {"chat_id": chat_id, "message_thread_id": message_thread_id, **kwargs},
        )

    async def close_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return self._record(
            "close_forum_topic",
            {"chat_id": chat_id, "message_thread_id": message_thread_id, **kwargs},
        )

    async def delete_forum_topic(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return self._record(
            "delete_forum_topic",
            {"chat_id": chat_id, "message_thread_id": message_thread_id, **kwargs},
        )

    async def unpin_all_forum_topic_messages(
        self, chat_id: int | str, message_thread_id: int, **kwargs: Any
    ) -> bool:
        return self._record(
            "unpin_all_forum_topic_messages",
            {"chat_id": chat_id, "message_thread_id": message_thread_id, **kwargs},
        )

    async def delete_my_commands(
        self,
        scope: BotCommandScope | None = None,
        language_code: str | None = None,
        **kwargs: Any,
    ) -> bool:
        return self._record(
            "delete_my_commands",
            {"scope": scope, "language_code": language_code, **kwargs},
        )

    async def set_my_commands(
        self,
        commands: Sequence[BotCommand | tuple[str, str]],
        scope: BotCommandScope | None = None,
        language_code: str | None = None,
        **kwargs: Any,
    ) -> bool:
        return self._record(
            "set_my_commands",
            {
                "commands": commands,
                "scope": scope,
                "language_code": language_code,
                **kwargs,
            },
        )


_DEFAULT_RETURNS: dict[str, Any] = {
    "delete_message": True,
    "send_chat_action": True,
    "set_message_reaction": True,
    "edit_forum_topic": True,
    "close_forum_topic": True,
    "delete_forum_topic": True,
    "unpin_all_forum_topic_messages": True,
    "delete_my_commands": True,
    "set_my_commands": True,
}


def unwrap_bot(client: TelegramClient) -> Bot:
    if isinstance(client, PTBTelegramClient):
        return client.bot
    return cast("Bot", client)


__all__ = [
    "FakeTelegramClient",
    "PTBTelegramClient",
    "TelegramClient",
    "unwrap_bot",
]
