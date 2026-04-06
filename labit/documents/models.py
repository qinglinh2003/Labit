from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator


class DocStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class ReviewAction(str, Enum):
    AGREE = "agree"
    QUESTION = "question"
    SUPPLEMENT = "supplement"
    DISCUSS = "discuss"
    OPPOSE = "oppose"


class DocUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str
    markdown: str

    @field_validator("title", "summary", "markdown", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("title", "summary", "markdown")
    @classmethod
    def validate_required(cls, value: str) -> str:
        if not value:
            raise ValueError("This field cannot be empty.")
        return value


@dataclass(frozen=True)
class DocSession:
    project: str
    doc_id: str
    title: str
    status: DocStatus
    document_path: str
    log_path: str
    source: str
    created_at: str
    updated_at: str
    iteration: int = 0
