"""Pydantic models for the Code module."""
from __future__ import annotations

from pydantic import BaseModel

from labit.api.chat_models import (  # noqa: F401
    AgentName,
    AskRequest as CodeAskRequest,
    Artifact,
    Attachment,
    ChatListItem as CodeChatListItem,
    ChatMessage as CodeChatMessage,
    ChatMode,
    ChatRecord as CodeChatRecord,
    CreateChatRequest as CreateCodeChatRequest,
    UpdateChatRequest as UpdateCodeChatRequest,
)


class CodeFileRecord(BaseModel):
    """A file or directory entry in the code tree."""
    name: str
    path: str  # relative path under code/
    is_dir: bool = False
    size_bytes: int = 0
    language: str = ""  # e.g. "python", "typescript", "markdown"


class CodeFileContent(BaseModel):
    content: str


class CodeTreeEntry(BaseModel):
    name: str
    path: str  # relative path under code/
    is_dir: bool = False
    children: list[CodeTreeEntry] | None = None
