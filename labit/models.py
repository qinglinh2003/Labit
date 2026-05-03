from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    def to_yaml_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ProjectSummary(BaseModel):
    name: str
    description: str
    keyword_count: int
    is_active: bool
    config_path: str
