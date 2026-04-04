from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from labit.chat.models import short_id, utc_now_iso


class MemoryType(str, Enum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"


class MemoryKind(str, Enum):
    PROJECT_FRAME = "project_frame"
    DECISION = "decision"
    OPEN_LOOP = "open_loop"
    PAPER_TAKEAWAY = "paper_takeaway"
    DISCUSSION_TAKEAWAY = "discussion_takeaway"
    INVESTIGATION_FINDING = "investigation_finding"
    EXPERIMENT_OUTCOME = "experiment_outcome"
    CODE_FACT = "code_fact"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


class MemoryNamespace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parts: tuple[str, ...]

    @field_validator("parts")
    @classmethod
    def validate_parts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(part.strip() for part in value if part and part.strip())
        if not cleaned:
            raise ValueError("Memory namespace cannot be empty.")
        return cleaned

    def render(self) -> str:
        return "/".join(self.parts)


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(default_factory=short_id)
    project: str
    namespace: MemoryNamespace
    kind: MemoryKind
    memory_type: MemoryType
    title: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    source_artifact_refs: list[str] = Field(default_factory=list)
    confidence: str = "medium"
    status: MemoryStatus = MemoryStatus.ACTIVE
    promotion_score: int = 0
    promotion_reasons: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    superseded_at: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("project", "title", "summary", "confidence")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty.")
        return value
