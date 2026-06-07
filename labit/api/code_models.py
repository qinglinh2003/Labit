"""Pydantic models for the Code module."""
from __future__ import annotations

from pydantic import BaseModel, Field

from labit.api.chat_models import AgentName, Artifact, ChatMode


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


class CodeChatMessage(BaseModel):
    id: str
    role: str
    content: str
    agent: str | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    created_at: str


class CodeChatRecord(BaseModel):
    chat_id: str
    title: str
    file_path: str | None = None  # optional: relative path of a file being discussed
    mode: ChatMode = ChatMode.SINGLE
    first_agent: AgentName = "claude"
    participants: list[str] = Field(default_factory=lambda: ["claude", "codex"])
    created_at: str
    updated_at: str
    messages: list[CodeChatMessage] = Field(default_factory=list)


class CodeChatListItem(BaseModel):
    chat_id: str
    title: str
    mode: ChatMode
    first_agent: str
    updated_at: str
    message_count: int


class CreateCodeChatRequest(BaseModel):
    file_path: str | None = None  # optional: relative path of a file
    title: str = ""
    mode: ChatMode = ChatMode.SINGLE
    first_agent: AgentName = "claude"


class CodeAskRequest(BaseModel):
    content: str


class UpdateCodeChatRequest(BaseModel):
    mode: ChatMode | None = None
    first_agent: AgentName | None = None
    title: str | None = None
