from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, field_validator


@dataclass(frozen=True)
class CaptureRecord:
    kind: str
    title: str
    path: str
    source: str
    created_at: str


class IdeaDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    summary_markdown: str
    key_question: str

    @field_validator("title", "summary_markdown", "key_question", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("title", "summary_markdown", "key_question")
    @classmethod
    def validate_required(cls, value: str) -> str:
        if not value:
            raise ValueError("This field cannot be empty.")
        return value
