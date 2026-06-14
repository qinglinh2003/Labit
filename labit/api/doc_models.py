"""Pydantic models for the Docs module."""
from __future__ import annotations

from pydantic import BaseModel, Field

from labit.api.chat_models import (  # noqa: F401
    AgentName,
    AskRequest as DocAskRequest,
    Artifact,
    ChatListItem as DocChatListItem,
    ChatMessage as DocChatMessage,
    ChatMode,
    ChatRecord as DocChatRecord,
    CreateChatRequest as CreateDocChatRequest,
    UpdateChatRequest as UpdateDocChatRequest,
)


class DocRecord(BaseModel):
    id: str  # base64url-encoded relative path
    title: str
    filename: str
    path: str  # relative path under docs/
    format: str  # "markdown" | "text" | "latex" | "json" | "yaml" | "python" | "unknown"
    editable: bool = True
    size_bytes: int = 0
    modified_at: str = ""
    tags: list[str] = Field(default_factory=list)


class DocContent(BaseModel):
    content: str


class CreateDocRequest(BaseModel):
    filename: str  # e.g. "ideas/new-idea.md"
    content: str = ""
