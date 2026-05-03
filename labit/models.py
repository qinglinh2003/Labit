from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _validate_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Name cannot be empty.")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(ch not in allowed for ch in value):
        raise ValueError(
            "Name may only contain letters, numbers, '.', '_' and '-'."
        )
    return value



def _strip_required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("This field cannot be empty.")
    return value



def _strip_optional_text(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()



def _validate_repo(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    valid_prefixes = ("git@", "https://", "http://", "ssh://")
    if not value.startswith(valid_prefixes):
        path = Path(value).expanduser()
        if not path.exists():
            raise ValueError(
                "Repository URL must start with git@, https://, http://, or ssh://, "
                "or point to an existing local repository path."
            )
    return value



def _normalize_keywords(values: list[str]) -> list[str]:
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



def _extract_phrases(text: str, *, max_items: int = 12) -> list[str]:
    parts = re.split(r"[\n,;]", text)
    phrases: list[str] = []
    seen: set[str] = set()
    for part in parts:
        item = part.strip(" .:-")
        if len(item) < 3:
            continue
        key = item.lower()
        if key in seen:
            continue
        phrases.append(item)
        seen.add(key)
        if len(phrases) >= max_items:
            break
    return phrases


class ProjectSeed(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    repo: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name_field(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, value: str | None) -> str | None:
        return _validate_repo(value)

    def to_yaml_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class SemanticBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    paper_scope: str
    methods_of_interest: str | None = None
    exclusions: str | None = None
    notes: str | None = None

    @field_validator("goal", "paper_scope")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return _strip_required_text(value)

    @field_validator("methods_of_interest", "exclusions", "notes")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ProjectDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    relevance_criteria: str = ""

    @field_validator("description", "relevance_criteria")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str:
        return _strip_optional_text(value)

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        return _normalize_keywords(values)

    @classmethod
    def scaffold_from_brief(cls, brief: "SemanticBrief") -> "ProjectDraft":
        keyword_source = brief.methods_of_interest or brief.paper_scope or brief.goal
        keywords = _extract_phrases(keyword_source)
        if not keywords:
            keywords = _extract_phrases(brief.goal)

        relevance_parts = [brief.paper_scope]
        if brief.methods_of_interest:
            relevance_parts.append(f"Methods of interest: {brief.methods_of_interest}.")
        if brief.exclusions:
            relevance_parts.append(f"Out of scope: {brief.exclusions}.")

        return cls(
            description=brief.goal,
            keywords=keywords[:12],
            relevance_criteria=" ".join(part.strip() for part in relevance_parts if part.strip()),
        )

    def to_yaml_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ProjectSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    repo: str | None = None
    keywords: list[str] = Field(default_factory=list)
    relevance_criteria: str = ""

    @field_validator("name")
    @classmethod
    def validate_name_field(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("description", "relevance_criteria")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str:
        return _strip_optional_text(value)

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, value: str | None) -> str | None:
        return _validate_repo(value)

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        return _normalize_keywords(values)

    @classmethod
    def from_seed_and_draft(cls, seed: ProjectSeed, draft: ProjectDraft) -> "ProjectSpec":
        return cls(
            name=seed.name,
            repo=seed.repo,
            description=draft.description,
            keywords=draft.keywords,
            relevance_criteria=draft.relevance_criteria,
        )

    def to_seed(self) -> ProjectSeed:
        return ProjectSeed(
            name=self.name,
            repo=self.repo,
        )

    def to_draft(self) -> ProjectDraft:
        return ProjectDraft(
            description=self.description,
            keywords=self.keywords,
            relevance_criteria=self.relevance_criteria,
        )

    def to_yaml_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ProjectSummary(BaseModel):
    name: str
    description: str
    keyword_count: int
    is_active: bool
    config_path: str
