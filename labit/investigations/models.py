from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from labit.chat.models import ChatMode, utc_now_iso


class InvestigationReportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    path: str
    date: str = ""
    status: str = ""
    topic: str = ""
    summary: str = ""
    score: int = 0


class InvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    topic: str
    mode: ChatMode
    run_id: str
    report_path: str
    title: str
    summary: str = ""
    related_reports: list[InvestigationReportSummary] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("project", "topic", "run_id", "report_path", "title")
    @classmethod
    def validate_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty.")
        return value

