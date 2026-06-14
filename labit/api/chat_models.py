"""Unified Pydantic models for all chat types (general, paper, code, doc)."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_serializer


class ChatMode(str, Enum):
    SINGLE = "single"
    PARALLEL = "parallel"
    ROUND_ROBIN = "round_robin"


AgentName = Literal["claude", "codex"]


class Attachment(BaseModel):
    id: str
    kind: str = "image"  # currently only "image"
    filename: str
    mime_type: str
    path: str  # absolute path on disk


class Artifact(BaseModel):
    id: str
    title: str
    filename: str
    language: str = ""
    mime_type: str = "text/plain"
    content: str  # the full artifact content
    file_path: str | None = None  # relative path within chat dir, e.g. "artifacts/proposal.md"


class ChatMessage(BaseModel):
    id: str
    role: str  # "user" or "assistant"
    content: str
    agent: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    created_at: str


class ChatRecord(BaseModel):
    chat_id: str
    title: str
    mode: ChatMode = ChatMode.SINGLE
    first_agent: AgentName = "claude"
    participants: list[str] = Field(default_factory=lambda: ["claude", "codex"])
    created_at: str
    updated_at: str
    messages: list[ChatMessage] = Field(default_factory=list)
    # Domain context — each chat type uses the relevant field(s)
    project: str | None = None
    paper_id: str | None = None
    doc_id: str | None = None
    file_path: str | None = None

    @model_serializer(mode="wrap")
    def _exclude_none(self, handler):
        data = handler(self)
        return {k: v for k, v in data.items() if v is not None}


class ChatListItem(BaseModel):
    chat_id: str
    title: str
    mode: ChatMode
    first_agent: str
    updated_at: str
    message_count: int


class CreateChatRequest(BaseModel):
    title: str = ""
    mode: ChatMode = ChatMode.SINGLE
    first_agent: AgentName = "claude"
    file_path: str | None = None  # code chat: optional relative path of a file


class AskRequest(BaseModel):
    content: str
    attachment_ids: list[str] = Field(default_factory=list)


class UpdateChatRequest(BaseModel):
    mode: ChatMode | None = None
    first_agent: AgentName | None = None
    title: str | None = None
