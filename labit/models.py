from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ComputeBackend(str, Enum):
    SSH = "ssh"


class StorageBackend(str, Enum):
    RCLONE = "rclone"


class SSHConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str = "root"
    host: str
    port: int = 22
    ssh_key: str | None = None

    @field_validator("user")
    @classmethod
    def validate_user(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("SSH user cannot be empty.")
        return value

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("SSH host cannot be empty.")
        return value

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if value < 1 or value > 65535:
            raise ValueError("SSH port must be between 1 and 65535.")
        return value

    @field_validator("ssh_key")
    @classmethod
    def strip_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ComputeWorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workdir: str
    datadir: str | None = None

    @field_validator("workdir")
    @classmethod
    def validate_workdir(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Workdir cannot be empty.")
        return value

    @field_validator("datadir")
    @classmethod
    def strip_datadir(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ComputeSetupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script: str = ""

    @field_validator("script")
    @classmethod
    def strip_script(cls, value: str) -> str:
        return value.strip()


class ComputeHardwareConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gpu_count: int = 0
    gpu_type: str | None = None

    @field_validator("gpu_count")
    @classmethod
    def validate_gpu_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("GPU count cannot be negative.")
        return value

    @field_validator("gpu_type")
    @classmethod
    def strip_gpu_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ComputeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    backend: ComputeBackend = ComputeBackend.SSH
    connection: SSHConnectionConfig
    workspace: ComputeWorkspaceConfig
    setup: ComputeSetupConfig = Field(default_factory=ComputeSetupConfig)
    hardware: ComputeHardwareConfig = Field(default_factory=ComputeHardwareConfig)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_name(value)



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



def _normalize_arxiv_categories(values: list[str]) -> list[str]:
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



def _normalize_sync_dirs(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip().strip("/")
        if not item:
            continue
        if item.startswith(".") or item.startswith("~") or "/" in item:
            raise ValueError("Sync directories must be simple project-relative directory names.")
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
    model_config = ConfigDict(extra="forbid")

    name: str
    repo: str | None = None
    compute_profile: str
    storage_profile: str
    sync_dirs: list[str] = Field(default_factory=list)

    @field_validator("name", "compute_profile", "storage_profile")
    @classmethod
    def validate_name_fields(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, value: str | None) -> str | None:
        return _validate_repo(value)

    @field_validator("sync_dirs")
    @classmethod
    def normalize_sync_dirs(cls, values: list[str]) -> list[str]:
        return _normalize_sync_dirs(values)

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
    arxiv_categories: list[str] = Field(default_factory=list)
    relevance_criteria: str = ""

    @field_validator("description", "relevance_criteria")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str:
        return _strip_optional_text(value)

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        return _normalize_keywords(values)

    @field_validator("arxiv_categories")
    @classmethod
    def normalize_arxiv_categories(cls, values: list[str]) -> list[str]:
        return _normalize_arxiv_categories(values)

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
            arxiv_categories=[],
            relevance_criteria=" ".join(part.strip() for part in relevance_parts if part.strip()),
        )

    def to_yaml_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ProjectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    repo: str | None = None
    keywords: list[str] = Field(default_factory=list)
    arxiv_categories: list[str] = Field(default_factory=list)
    relevance_criteria: str = ""
    compute_profile: str
    storage_profile: str
    sync_dirs: list[str] = Field(default_factory=list)

    @field_validator("name", "compute_profile", "storage_profile")
    @classmethod
    def validate_name_fields(cls, value: str) -> str:
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

    @field_validator("arxiv_categories")
    @classmethod
    def normalize_arxiv_categories(cls, values: list[str]) -> list[str]:
        return _normalize_arxiv_categories(values)

    @field_validator("sync_dirs")
    @classmethod
    def normalize_sync_dirs(cls, values: list[str]) -> list[str]:
        return _normalize_sync_dirs(values)

    @classmethod
    def from_seed_and_draft(cls, seed: ProjectSeed, draft: ProjectDraft) -> "ProjectSpec":
        return cls(
            name=seed.name,
            repo=seed.repo,
            compute_profile=seed.compute_profile,
            storage_profile=seed.storage_profile,
            sync_dirs=seed.sync_dirs,
            description=draft.description,
            keywords=draft.keywords,
            arxiv_categories=draft.arxiv_categories,
            relevance_criteria=draft.relevance_criteria,
        )

    def to_seed(self) -> ProjectSeed:
        return ProjectSeed(
            name=self.name,
            repo=self.repo,
            compute_profile=self.compute_profile,
            storage_profile=self.storage_profile,
            sync_dirs=self.sync_dirs,
        )

    def to_draft(self) -> ProjectDraft:
        return ProjectDraft(
            description=self.description,
            keywords=self.keywords,
            arxiv_categories=self.arxiv_categories,
            relevance_criteria=self.relevance_criteria,
        )

    def to_yaml_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ProjectSummary(BaseModel):
    name: str
    description: str
    keyword_count: int
    paper_count: int
    hypothesis_count: int
    is_active: bool
    config_path: str


class RcloneStorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote: str
    bucket: str

    @field_validator("remote", "bucket")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return _strip_required_text(value)


class StorageLayoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_template: str = "{project}/{dir}"

    @field_validator("path_template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        value = _strip_required_text(value)
        allowed = {"{project}", "{dir}"}
        used = {token for token in allowed if token in value}
        if "{project}" not in used or "{dir}" not in used:
            raise ValueError("Storage path template must include both '{project}' and '{dir}'.")
        return value


class StoragePolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "compute-managed"

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        value = _strip_required_text(value)
        if value != "compute-managed":
            raise ValueError("Storage policy mode must be 'compute-managed'.")
        return value


class StorageProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    backend: StorageBackend = StorageBackend.RCLONE
    rclone: RcloneStorageConfig
    layout: StorageLayoutConfig = Field(default_factory=StorageLayoutConfig)
    policy: StoragePolicyConfig = Field(default_factory=StoragePolicyConfig)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_name(value)
