from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SyncDirection(str, Enum):
    PUSH = "push"
    PULL = "pull"


class SyncSize(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bytes: int | None = None
    count: int | None = None
    error: str | None = None


class SyncStatusEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir_name: str
    compute_path: str
    remote_path: str
    compute: SyncSize = Field(default_factory=SyncSize)
    remote: SyncSize = Field(default_factory=SyncSize)

    @field_validator("dir_name", "compute_path", "remote_path", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value


class SyncTransferEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir_name: str
    direction: SyncDirection
    compute_path: str
    remote_path: str
    ok: bool
    exit_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""

    @field_validator("dir_name", "compute_path", "remote_path", "stdout_tail", "stderr_tail", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value
