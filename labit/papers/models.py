from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_title(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return re.sub(r"\s+", " ", lowered)


def normalize_paper_id(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Paper id cannot be empty.")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-:")
    if any(ch not in allowed for ch in value):
        raise ValueError(
            "Paper id may only contain letters, numbers, '.', '_', '-', and ':'."
        )
    return value


def _clean_text(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


def _clean_optional_url(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _clean_authors(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        cleaned.append(item)
        seen.add(key)
    return cleaned


def _clean_projects(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        cleaned.append(item)
        seen.add(key)
    return cleaned


class DuplicateStatus(str, Enum):
    NEW = "new"
    IN_GLOBAL = "in_global"
    IN_PROJECT = "in_project"
    IN_GLOBAL_AND_PROJECT = "in_global_and_project"


class PaperContentFormat(str, Enum):
    HTML = "html"
    PDF = "pdf"


class ProjectPaperStatus(str, Enum):
    PULLED = "pulled"
    INGESTED = "ingested"


class SearchMode(str, Enum):
    SINGLE = "single"
    DISCUSSION = "discussion"


class SearchScope(str, Enum):
    BROAD = "broad"
    NARROW = "narrow"


class PaperExternalIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arxiv: str | None = None
    doi: str | None = None
    openalex: str | None = None
    semantic_scholar: str | None = Field(default=None, alias="semanticScholar")
    custom: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def strip_values(self) -> "PaperExternalIds":
        if self.arxiv is not None:
            self.arxiv = self.arxiv.strip() or None
        if self.doi is not None:
            self.doi = self.doi.strip() or None
        if self.openalex is not None:
            self.openalex = self.openalex.strip() or None
        if self.semantic_scholar is not None:
            self.semantic_scholar = self.semantic_scholar.strip() or None
        self.custom = {
            key.strip(): value.strip()
            for key, value in self.custom.items()
            if key.strip() and value.strip()
        }
        return self

    def values(self) -> list[str]:
        values = [self.arxiv, self.doi, self.openalex, self.semantic_scholar]
        values.extend(self.custom.values())
        return [value for value in values if value]


class GlobalPaperMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    source: str | None = None
    url: str | None = None
    html_url: str | None = None
    pdf_url: str | None = None
    content_format: PaperContentFormat | None = None
    external_ids: PaperExternalIds = Field(default_factory=PaperExternalIds)
    relevance_to: list[str] = Field(default_factory=list)
    added_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("paper_id")
    @classmethod
    def validate_paper_id(cls, value: str) -> str:
        return normalize_paper_id(value)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Paper title cannot be empty.")
        return value

    @field_validator("authors")
    @classmethod
    def normalize_authors(cls, values: list[str]) -> list[str]:
        return _clean_authors(values)

    @field_validator("venue", "source")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        value = _clean_text(value)
        return value or None

    @field_validator("url", "html_url", "pdf_url")
    @classmethod
    def strip_optional_url(cls, value: str | None) -> str | None:
        return _clean_optional_url(value)

    @field_validator("relevance_to")
    @classmethod
    def normalize_projects(cls, values: list[str]) -> list[str]:
        return _clean_projects(values)


class GlobalPaperIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str
    normalized_title: str
    title_aliases: list[str] = Field(default_factory=list)
    year: int | None = None
    external_ids: PaperExternalIds = Field(default_factory=PaperExternalIds)
    path: str
    linked_projects: list[str] = Field(default_factory=list)

    @field_validator("paper_id")
    @classmethod
    def validate_paper_id(cls, value: str) -> str:
        return normalize_paper_id(value)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Paper title cannot be empty.")
        return value

    @field_validator("normalized_title")
    @classmethod
    def validate_normalized_title(cls, value: str) -> str:
        value = normalize_title(value)
        if not value:
            raise ValueError("normalized_title cannot be empty.")
        return value

    @field_validator("title_aliases", "linked_projects")
    @classmethod
    def normalize_lists(cls, values: list[str]) -> list[str]:
        return _clean_projects(values)


class GlobalPaperIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    papers: list[GlobalPaperIndexEntry] = Field(default_factory=list)


class ProjectPaperRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    project: str
    title: str
    global_dir: str
    meta_path: str
    html_path: str | None = None
    pdf_path: str | None = None
    status: ProjectPaperStatus
    summary_path: str | None = None
    notes_path: str | None = None
    added_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("paper_id")
    @classmethod
    def validate_paper_id(cls, value: str) -> str:
        return normalize_paper_id(value)

    @field_validator("project", "title", "global_dir", "meta_path")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty.")
        return value


class ProjectPaperIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str
    path: str
    status: ProjectPaperStatus
    added_at: str = Field(default_factory=utc_now_iso)

    @field_validator("paper_id")
    @classmethod
    def validate_paper_id(cls, value: str) -> str:
        return normalize_paper_id(value)


class ProjectPaperIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    papers: list[ProjectPaperIndexEntry] = Field(default_factory=list)

    @field_validator("project")
    @classmethod
    def validate_project(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project cannot be empty.")
        return value


class GlobalPaperRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: GlobalPaperMeta
    global_dir: str
    html_path: str | None = None
    pdf_path: str | None = None
    linked_projects: list[str] = Field(default_factory=list)


class PaperLibraryOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_project: str | None = None
    global_paper_count: int = 0
    project_paper_count: int = 0
    project_papers: list[ProjectPaperIndexEntry] = Field(default_factory=list)


class DuplicateMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DuplicateStatus
    paper_id: str | None = None
    reason: str | None = None
    project: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaperSearchIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    focus: str = ""
    scope: SearchScope = SearchScope.NARROW
    mode: SearchMode = SearchMode.SINGLE
    limit: int = 6

    @field_validator("query", "focus")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, value: int) -> int:
        if value < 1 or value > 20:
            raise ValueError("Search limit must be between 1 and 20.")
        return value


class PaperSearchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    arxiv_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    url: str
    html_url: str
    pdf_url: str
    one_line_description: str = ""
    why_relevant: str = ""
    duplicate_status: DuplicateStatus = DuplicateStatus.NEW
    duplicate_reason: str | None = None
    rank: int | None = None
    score: float | None = None
    retrieval_sources: list[str] = Field(default_factory=list)

    @field_validator("paper_id")
    @classmethod
    def validate_paper_id(cls, value: str) -> str:
        return normalize_paper_id(value)

    @field_validator("title", "url", "html_url", "pdf_url", "one_line_description", "why_relevant", "abstract")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()
