from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProviderKind(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"


class AgentRole(str, Enum):
    DISCUSSANT = "discussant"
    WRITER = "writer"


class CodeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_code_dir: str
    readme_excerpt: str = ""
    package_roots: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    prompt: str
    system_prompt: str | None = None
    output_schema: dict[str, Any] | None = None
    cwd: str | None = None
    session_id: str | None = None
    timeout_seconds: int | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    image_paths: list[str] = Field(default_factory=list)
    extra_args: list[str] = Field(default_factory=list)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Prompt cannot be empty.")
        return value


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderKind
    raw_output: str
    structured_output: dict[str, Any] | list[Any] | str | None = None
    session_id: str | None = None
    command: list[str] = Field(default_factory=list)
