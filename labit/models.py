from __future__ import annotations

import shlex
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



class SSHConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str
    host: str
    port: int = 22
    identity_file: str | None = None

    @field_validator("user", "host")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("SSH user and host cannot be empty.")
        return value

    @field_validator("identity_file")
    @classmethod
    def normalize_identity_file(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if value < 1 or value > 65535:
            raise ValueError("SSH port must be between 1 and 65535.")
        return value

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}"


class ComputeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    connection: SSHConnection
    workdir: str = ""
    notes: str = ""

    @field_validator("name")
    @classmethod
    def validate_name_field(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("workdir", "notes")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str:
        return _strip_optional_text(value)

    def ssh_command(self) -> list[str]:
        command = ["ssh"]
        if self.connection.identity_file:
            command.extend(["-i", self.connection.identity_file])
        if self.connection.port != 22:
            command.extend(["-p", str(self.connection.port)])
        command.append(self.connection.target)
        return command

    def ssh_display(self) -> str:
        return shlex.join(self.ssh_command())


class ProjectSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    repo: str | None = None
    keywords: list[str] = Field(default_factory=list)
    relevance_criteria: str = ""
    compute_profiles: list[ComputeProfile] = Field(default_factory=list)

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

    @field_validator("compute_profiles")
    @classmethod
    def validate_compute_profile_names(cls, values: list[ComputeProfile]) -> list[ComputeProfile]:
        seen: set[str] = set()
        for profile in values:
            key = profile.name.lower()
            if key in seen:
                raise ValueError(f"Duplicate compute profile name: {profile.name}")
            seen.add(key)
        return values

    def to_yaml_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ProjectSummary(BaseModel):
    name: str
    description: str
    keyword_count: int
    compute_count: int
    is_active: bool
    config_path: str
