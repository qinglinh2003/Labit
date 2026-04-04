from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from labit.agents.models import ProviderKind


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def short_id() -> str:
    return uuid4().hex[:12]


class ChatMode(str, Enum):
    SINGLE = "single"
    ROUND_ROBIN = "round_robin"
    PARALLEL = "parallel"


class ChatStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class MessageType(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class ContextBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    config: dict = Field(default_factory=dict)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Context provider cannot be empty.")
        return value


class MemoryBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    config: dict = Field(default_factory=dict)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Memory provider cannot be empty.")
        return value


class ChatParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    provider: ProviderKind

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Participant name cannot be empty.")
        return value


class ContextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    title: str
    content: str


class MemoryBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    title: str
    content: str


class ContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[ContextBlock] = Field(default_factory=list)
    memory: list[MemoryBlock] = Field(default_factory=list)
    built_at: str = Field(default_factory=utc_now_iso)


class ChatSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(default_factory=short_id)
    title: str
    mode: ChatMode
    status: ChatStatus = ChatStatus.ACTIVE
    project: str | None = None
    participants: list[ChatParticipant]
    context_bindings: list[ContextBinding] = Field(default_factory=list)
    memory_bindings: list[MemoryBinding] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Session title cannot be empty.")
        return value


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(default_factory=short_id)
    session_id: str
    turn_index: int
    message_type: MessageType
    speaker: str
    provider: ProviderKind | None = None
    content: str
    reply_to: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("session_id", "speaker", "content")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty.")
        return value


class ChatReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant: ChatParticipant
    message: ChatMessage


class DiscussionSynthesisDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    consensus: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def strip_summary(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        if not value:
            raise ValueError("Summary cannot be empty.")
        return value

    @field_validator("consensus", "disagreements", "followups", mode="before")
    @classmethod
    def normalize_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split("\n")]
        if not isinstance(value, list):
            raise ValueError("This field must be a list of strings.")
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned
