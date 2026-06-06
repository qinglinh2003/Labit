"""Pydantic models for the Docs module."""
from __future__ import annotations

from pydantic import BaseModel, Field

from labit.api.chat_models import AgentName, Artifact, ChatMode


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


class DocChatMessage(BaseModel):
    id: str
    role: str
    content: str
    agent: str | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    created_at: str


class DocChatRecord(BaseModel):
    chat_id: str
    title: str
    doc_id: str
    mode: ChatMode = ChatMode.SINGLE
    first_agent: AgentName = "claude"
    participants: list[str] = Field(default_factory=lambda: ["claude", "codex"])
    created_at: str
    updated_at: str
    messages: list[DocChatMessage] = Field(default_factory=list)


class DocChatListItem(BaseModel):
    chat_id: str
    title: str
    mode: ChatMode
    first_agent: str
    updated_at: str
    message_count: int


class CreateDocChatRequest(BaseModel):
    title: str = ""
    mode: ChatMode = ChatMode.SINGLE
    first_agent: AgentName = "claude"


class DocAskRequest(BaseModel):
    content: str


class UpdateDocChatRequest(BaseModel):
    mode: ChatMode | None = None
    first_agent: AgentName | None = None
    title: str | None = None
