from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from labit.chat.models import short_id, utc_now_iso


class SessionEventKind(str, Enum):
    MESSAGE_USER = "message.user"
    MESSAGE_AGENT = "message.agent"
    MESSAGE_SYSTEM = "message.system"
    ARTIFACT_IDEA_CREATED = "artifact.idea_created"
    ARTIFACT_TODO_CREATED = "artifact.todo_created"
    ARTIFACT_DOCUMENT_CREATED = "artifact.document_created"
    ARTIFACT_DOCUMENT_UPDATED = "artifact.document_updated"
    DISCUSSION_SYNTHESIS = "discussion.synthesis"


class SessionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=short_id)
    session_id: str
    project: str | None = None
    kind: SessionEventKind
    turn_index: int | None = None
    actor: str
    summary: str
    payload: dict = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("session_id", "actor", "summary")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty.")
        return value


class DiscussionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consensus: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)


class WorkingMemorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    project: str | None = None
    current_goal: str = ""
    active_artifacts: list[str] = Field(default_factory=list)
    decisions_made: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    discussion_state: DiscussionState = Field(default_factory=DiscussionState)
    built_from_event_ids: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)
