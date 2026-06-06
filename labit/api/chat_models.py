"""Pydantic models for paper-scoped LLM chat."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ChatMode(str, Enum):
    SINGLE = "single"
    PARALLEL = "parallel"
    ROUND_ROBIN = "round_robin"


AgentName = Literal["claude", "codex"]


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
    artifacts: list[Artifact] = Field(default_factory=list)
    created_at: str


class ChatRecord(BaseModel):
    chat_id: str
    title: str
    paper_id: str
    mode: ChatMode = ChatMode.SINGLE
    first_agent: AgentName = "claude"
    participants: list[str] = Field(default_factory=lambda: ["claude", "codex"])
    created_at: str
    updated_at: str
    messages: list[ChatMessage] = Field(default_factory=list)


class CreateChatRequest(BaseModel):
    title: str = ""
    mode: ChatMode = ChatMode.SINGLE
    first_agent: AgentName = "claude"


class AskRequest(BaseModel):
    content: str


class UpdateChatRequest(BaseModel):
    mode: ChatMode | None = None
    first_agent: AgentName | None = None
    title: str | None = None


class ChatListItem(BaseModel):
    chat_id: str
    title: str
    mode: ChatMode
    first_agent: str
    updated_at: str
    message_count: int
