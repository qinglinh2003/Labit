from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console

from labit.chat.models import ChatSession
from labit.chat.service import ChatService
from labit.paths import RepoPaths


@dataclass(slots=True)
class ChatContext:
    """Shared state passed from the chat shell into command handlers."""

    console: Console
    paths: RepoPaths
    service: ChatService
    session: ChatSession

    @property
    def project(self) -> str | None:
        return self.session.project

