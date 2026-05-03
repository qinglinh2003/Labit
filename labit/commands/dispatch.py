from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from labit.commands.context import ChatContext


CommandHandler = Callable[[ChatContext, str], None]


@dataclass(slots=True)
class SlashCommandDispatcher:
    """Small registry for slash commands that have been moved out of chat.py."""

    _handlers: dict[str, CommandHandler] = field(default_factory=dict)

    def register(self, command: str, handler: CommandHandler) -> None:
        normalized = command.strip()
        if not normalized.startswith("/"):
            raise ValueError("Slash command names must start with '/'.")
        self._handlers[normalized] = handler

    def can_handle(self, command: str) -> bool:
        return command in self._handlers

    def handle(self, command: str, ctx: ChatContext, argument: str) -> bool:
        handler = self._handlers.get(command)
        if handler is None:
            return False
        handler(ctx, argument)
        return True

