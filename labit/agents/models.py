from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class ProviderKind(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"


class CollaborationMode(str, Enum):
    DISCUSSION = "discussion"
    WRITER_REVIEWER = "writer_reviewer"
    PARALLEL_WRITE = "parallel_write"


class AgentRole(str, Enum):
    DISCUSSANT = "discussant"
    WRITER = "writer"
    REVIEWER = "reviewer"
    SCOUT = "scout"
    NORMALIZER = "normalizer"
    SYNTHESIZER = "synthesizer"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ProjectSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    relevance_criteria: str = ""

    @field_validator("description", "relevance_criteria")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class MemorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recent_reports: list[dict[str, Any]] = Field(default_factory=list)
    open_hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    key_papers: list[dict[str, Any]] = Field(default_factory=list)
    global_matches: list[dict[str, Any]] = Field(default_factory=list)


class CodeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_code_dir: str
    readme_excerpt: str = ""
    package_roots: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class WorkspaceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_root: str
    run_dir: str | None = None
    allowed_write_scope: list[str] = Field(default_factory=list)


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    goal: str
    mode: CollaborationMode = CollaborationMode.DISCUSSION
    requires_mutation: bool = False
    expected_outputs: list[str] = Field(default_factory=list)
    write_scope: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind", "goal")
    @classmethod
    def validate_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be empty.")
        return value


class ContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectSnapshot | None = None
    task: TaskSpec
    memory: MemorySnapshot = Field(default_factory=MemorySnapshot)
    code: CodeSnapshot | None = None
    workspace: WorkspaceSnapshot
    extras: dict[str, Any] = Field(default_factory=dict)


class ProviderAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    provider: ProviderKind


class InputRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    path: str


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


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_kind: str
    mode: CollaborationMode
    status: RunStatus = RunStatus.PENDING
    project: str | None = None
    provider_assignments: list[ProviderAssignment] = Field(default_factory=list)
    started_at: str = Field(default_factory=utc_now_iso)
    finished_at: str | None = None


class RunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    run_id: str
    task_kind: str
    mode: CollaborationMode
    role: AgentRole
    provider: ProviderKind
    request_prompt: str
    request_system_prompt: str | None = None
    request_output_schema: dict[str, Any] | None = None
    request_cwd: str | None = None
    request_session_id: str | None = None
    request_timeout_seconds: int | None = None
    request_allowed_tools: list[str] = Field(default_factory=list)
    request_extra_args: list[str] = Field(default_factory=list)
    input_refs: list[InputRef] = Field(default_factory=list)
    output: dict[str, Any] | list[Any] | str | None = None
    raw_output: str = ""
    response_session_id: str | None = None
    command: list[str] = Field(default_factory=list)
    status: RunStatus = RunStatus.COMPLETED
    created_at: str = Field(default_factory=utc_now_iso)


class SynthesisArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    summary: str
    claims: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    recommended_next_step: str = ""
    mutation_plan: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class ExecutionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    performed_by: str
    action_kind: str
    write_targets: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
