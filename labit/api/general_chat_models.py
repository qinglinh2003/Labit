"""Pydantic models for project-scoped general LLM chat."""
from __future__ import annotations

from pydantic import BaseModel, Field

from labit.api.chat_models import AgentName, ChatMode


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


class GeneralChatMessage(BaseModel):
    id: str
    role: str  # "user" or "assistant"
    content: str
    agent: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    created_at: str


class GeneralChatRecord(BaseModel):
    chat_id: str
    title: str
    project: str
    mode: ChatMode = ChatMode.SINGLE
    first_agent: AgentName = "claude"
    participants: list[str] = Field(default_factory=lambda: ["claude", "codex"])
    created_at: str
    updated_at: str
    messages: list[GeneralChatMessage] = Field(default_factory=list)


class GeneralChatListItem(BaseModel):
    chat_id: str
    title: str
    mode: ChatMode
    first_agent: str
    updated_at: str
    message_count: int


class CreateGeneralChatRequest(BaseModel):
    title: str = ""
    mode: ChatMode = ChatMode.SINGLE
    first_agent: AgentName = "claude"


class GeneralAskRequest(BaseModel):
    content: str
    attachment_ids: list[str] = Field(default_factory=list)


class UpdateGeneralChatRequest(BaseModel):
    mode: ChatMode | None = None
    first_agent: AgentName | None = None
    title: str | None = None
