from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class HypothesisStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    ARCHIVED = "archived"


class HypothesisState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class HypothesisResolution(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


class HypothesisRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    project: str
    title: str
    claim: str
    state: HypothesisState = HypothesisState.OPEN
    resolution: HypothesisResolution = HypothesisResolution.PENDING
    status: HypothesisStatus = HypothesisStatus.DRAFT
    motivation: str = ""
    independent_variable: str = ""
    dependent_variable: str = ""
    success_criteria: str = ""
    failure_criteria: str = ""
    result_summary: str = ""
    decision_rationale: str = ""
    supporting_experiment_ids: list[str] = Field(default_factory=list)
    contradicting_experiment_ids: list[str] = Field(default_factory=list)
    closed_at: str | None = None
    source_session_id: str | None = None
    source_paper_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator(
        "hypothesis_id",
        "project",
        "title",
        "claim",
        "motivation",
        "independent_variable",
        "dependent_variable",
        "success_criteria",
        "failure_criteria",
        "result_summary",
        "decision_rationale",
        "closed_at",
        "source_session_id",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("title", "claim")
    @classmethod
    def validate_required(cls, value: str) -> str:
        if not value:
            raise ValueError("This field cannot be empty.")
        return value

    @field_validator("source_paper_ids", mode="before")
    @classmethod
    def normalize_source_papers(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",")]
        if not isinstance(value, list):
            raise ValueError("source_paper_ids must be a list of strings.")
        cleaned: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    @field_validator("supporting_experiment_ids", "contradicting_experiment_ids", mode="before")
    @classmethod
    def normalize_experiment_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",")]
        if not isinstance(value, list):
            raise ValueError("experiment id fields must be a list of strings.")
        cleaned: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    @model_validator(mode="after")
    def synchronize_status_fields(self) -> "HypothesisRecord":
        if self.state == HypothesisState.OPEN and self.resolution == HypothesisResolution.PENDING:
            if self.status == HypothesisStatus.SUPPORTED:
                self.state = HypothesisState.CLOSED
                self.resolution = HypothesisResolution.VALIDATED
            elif self.status == HypothesisStatus.REJECTED:
                self.state = HypothesisState.CLOSED
                self.resolution = HypothesisResolution.REJECTED
            elif self.status == HypothesisStatus.INCONCLUSIVE:
                self.state = HypothesisState.CLOSED
                self.resolution = HypothesisResolution.INCONCLUSIVE
            elif self.status == HypothesisStatus.ARCHIVED:
                self.state = HypothesisState.CLOSED
                self.resolution = HypothesisResolution.INCONCLUSIVE

        self.status = self._derive_status(self.state, self.resolution, self.status)
        return self

    @staticmethod
    def _derive_status(
        state: HypothesisState,
        resolution: HypothesisResolution,
        current: HypothesisStatus,
    ) -> HypothesisStatus:
        if current == HypothesisStatus.ARCHIVED:
            return HypothesisStatus.ARCHIVED
        if state == HypothesisState.CLOSED:
            if resolution == HypothesisResolution.VALIDATED:
                return HypothesisStatus.SUPPORTED
            if resolution == HypothesisResolution.REJECTED:
                return HypothesisStatus.REJECTED
            return HypothesisStatus.INCONCLUSIVE
        if current == HypothesisStatus.ACTIVE:
            return HypothesisStatus.ACTIVE
        return HypothesisStatus.DRAFT


class HypothesisDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    claim: str
    motivation: str = ""
    independent_variable: str = ""
    dependent_variable: str = ""
    success_criteria: str = ""
    failure_criteria: str = ""
    rationale_markdown: str = ""
    experiment_plan_markdown: str = ""
    source_paper_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "title",
        "claim",
        "motivation",
        "independent_variable",
        "dependent_variable",
        "success_criteria",
        "failure_criteria",
        "rationale_markdown",
        "experiment_plan_markdown",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("title", "claim")
    @classmethod
    def validate_required(cls, value: str) -> str:
        if not value:
            raise ValueError("This field cannot be empty.")
        return value

    @field_validator("source_paper_ids", mode="before")
    @classmethod
    def normalize_source_papers(cls, value: object) -> list[str]:
        return HypothesisRecord.normalize_source_papers(value)

    def to_record(
        self,
        *,
        project: str,
        hypothesis_id: str,
        source_session_id: str | None = None,
    ) -> HypothesisRecord:
        return HypothesisRecord(
            hypothesis_id=hypothesis_id,
            project=project,
            title=self.title,
            claim=self.claim,
            motivation=self.motivation,
            independent_variable=self.independent_variable,
            dependent_variable=self.dependent_variable,
            success_criteria=self.success_criteria,
            failure_criteria=self.failure_criteria,
            source_session_id=source_session_id,
            source_paper_ids=self.source_paper_ids,
        )


class HypothesisSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    title: str
    state: HypothesisState
    resolution: HypothesisResolution
    status: HypothesisStatus
    result_summary: str = ""
    source_session_id: str | None = None
    source_paper_ids: list[str] = Field(default_factory=list)
    updated_at: str = ""
    path: str
    legacy: bool = False


class HypothesisDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record: HypothesisRecord
    rationale_markdown: str = ""
    experiment_plan_markdown: str = ""
    path: str
    legacy: bool = False
    raw_legacy: dict[str, Any] = Field(default_factory=dict)
