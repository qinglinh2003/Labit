from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class ExperimentParentType(str, Enum):
    HYPOTHESIS = "hypothesis"


class ExecutionBackend(str, Enum):
    LOCAL = "local"


class ExecutionRuntime(str, Enum):
    PLAIN = "plain"
    CONDA = "conda"
    UV = "uv"


class ExperimentStatus(str, Enum):
    PLANNED = "planned"
    APPROVED = "approved"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ANALYZED = "analyzed"


class ExperimentAssessment(str, Enum):
    PENDING = "pending"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    INCONCLUSIVE = "inconclusive"
    INVALID = "invalid"


class TaskKind(str, Enum):
    DATA_PREP = "data_prep"
    EXTRACT = "extract"
    TRAIN = "train"
    EVAL = "eval"
    ANALYSIS = "analysis"
    CUSTOM = "custom"


class ResearchRole(str, Enum):
    PREREQUISITE = "prerequisite"
    EVIDENCE = "evidence"
    SUPPORTING = "supporting"


class TaskStatus(str, Enum):
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class LaunchStatus(str, Enum):
    PREPARED = "prepared"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    FAILED = "failed"
    COLLECTED = "collected"


class SubmissionPhase(str, Enum):
    PREPARE = "prepare"
    SUBMIT = "submit"
    POLL = "poll"
    COLLECT = "collect"
    CANCEL = "cancel"


class SubmissionErrorKind(str, Enum):
    TRANSPORT_ERROR = "transport_error"
    RESOURCE_ERROR = "resource_error"
    TASK_SPEC_ERROR = "task_spec_error"
    RUNTIME_ERROR = "runtime_error"
    UNKNOWN = "unknown"


class HypothesisSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    title: str
    claim: str
    success_criteria: str = ""
    failure_criteria: str = ""

    @field_validator("hypothesis_id", "title", "claim", "success_criteria", "failure_criteria", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value


class ExperimentExecutionProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    backend: ExecutionBackend
    profile: str = "default"
    workdir: str = ""
    datadir: str = ""
    setup_script: str = ""

    @field_validator(
        "profile",
        "workdir",
        "datadir",
        "setup_script",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_runtime_requirements(self) -> "ExperimentExecutionProfile":
        return self


class TaskResources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = "default"
    gpu: str = ""
    cpu: str = ""
    memory: str = ""

    @field_validator("profile", "gpu", "cpu", "memory", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch: str = ""
    config_ref: str = ""
    entrypoint: str = ""
    command: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    output_dir: str = ""
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("branch", "config_ref", "entrypoint", "command", "output_dir", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("env", mode="before")
    @classmethod
    def normalize_env(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("env must be a mapping.")
        normalized: dict[str, str] = {}
        for key, item in value.items():
            text_key = str(key).strip()
            if not text_key:
                continue
            normalized[text_key] = "" if item is None else str(item).strip()
        return normalized

    @model_validator(mode="after")
    def validate_command_source(self) -> "TaskSpec":
        if not self.command and not self.entrypoint:
            raise ValueError("Task spec must include either command or entrypoint.")
        return self


class TaskRuntime(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pid: str | None = None
    assigned_gpu: str | None = None
    log_path: str | None = None
    wandb_run_id: str | None = None
    submitted_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    @field_validator(
        "pid",
        "assigned_gpu",
        "log_path",
        "wandb_run_id",
        "submitted_at",
        "started_at",
        "finished_at",
        mode="before",
    )
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class TaskResults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)
    summary: str = ""
    error: str = ""

    @field_validator("summary", "error", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("artifact_refs", mode="before")
    @classmethod
    def normalize_refs(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",")]
        if not isinstance(value, list):
            raise ValueError("artifact_refs must be a list of strings.")
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned


class TaskReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: ExperimentAssessment = ExperimentAssessment.PENDING
    rationale: str = ""

    @field_validator("rationale", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    experiment_id: str
    project: str
    title: str
    task_kind: TaskKind
    research_role: ResearchRole
    status: TaskStatus = TaskStatus.PLANNED
    depends_on: list[str] = Field(default_factory=list)
    spec: TaskSpec
    resources: TaskResources = Field(default_factory=TaskResources)
    runtime: TaskRuntime = Field(default_factory=TaskRuntime)
    results: TaskResults = Field(default_factory=TaskResults)
    review: TaskReview = Field(default_factory=TaskReview)
    latest_launch_id: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("task_id", "experiment_id", "project", "title", "latest_launch_id", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("depends_on", mode="before")
    @classmethod
    def normalize_depends_on(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",")]
        if not isinstance(value, list):
            raise ValueError("depends_on must be a list of task ids.")
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned


class ExperimentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    project: str
    parent_type: ExperimentParentType = ExperimentParentType.HYPOTHESIS
    parent_id: str
    title: str
    objective: str
    status: ExperimentStatus = ExperimentStatus.PLANNED
    assessment: ExperimentAssessment = ExperimentAssessment.PENDING
    hypothesis_snapshot: HypothesisSnapshot
    execution: ExperimentExecutionProfile
    result_summary: str = ""
    decision_rationale: str = ""
    evidence_task_ids: list[str] = Field(default_factory=list)
    prerequisite_task_ids: list[str] = Field(default_factory=list)
    source_session_id: str | None = None
    source_paper_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator(
        "experiment_id",
        "project",
        "parent_id",
        "title",
        "objective",
        "result_summary",
        "decision_rationale",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("source_session_id", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("evidence_task_ids", "prerequisite_task_ids", "source_paper_ids", mode="before")
    @classmethod
    def normalize_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",")]
        if not isinstance(value, list):
            raise ValueError("This field must be a list of strings.")
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned


class TaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    task_kind: TaskKind
    research_role: ResearchRole
    depends_on: list[str] = Field(default_factory=list)
    spec: TaskSpec
    resources: TaskResources = Field(default_factory=TaskResources)

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not value:
            raise ValueError("Task title cannot be empty.")
        return value


class ExperimentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    objective: str
    tasks: list[TaskDraft] = Field(default_factory=list)
    execution: ExperimentExecutionProfile
    source_session_id: str | None = None
    source_paper_ids: list[str] = Field(default_factory=list)

    @field_validator("title", "objective", "source_session_id", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("title", "objective")
    @classmethod
    def validate_required(cls, value: str) -> str:
        if not value:
            raise ValueError("This field cannot be empty.")
        return value

    @field_validator("source_paper_ids", mode="before")
    @classmethod
    def normalize_source_paper_ids(cls, value: object) -> list[str]:
        return ExperimentRecord.normalize_lists(value)


class FrozenLaunchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    workdir: str = ""
    output_dir: str = ""
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("command", "workdir", "output_dir", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        if not value:
            raise ValueError("Frozen launch spec command cannot be empty.")
        return value

    @field_validator("env", mode="before")
    @classmethod
    def normalize_env(cls, value: object) -> dict[str, str]:
        return TaskSpec.normalize_env(value)


class CodeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = ""
    branch: str = ""
    commit: str = ""
    dirty: bool = False
    patch_path: str | None = None

    @field_validator("repo", "branch", "commit", "patch_path", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class SubmissionReceipt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    accepted: bool
    phase: SubmissionPhase
    backend: ExecutionBackend
    pid: str | None = None
    assigned_gpu: str | None = None
    log_path: str | None = None
    stderr_tail: str = ""
    error_kind: SubmissionErrorKind | None = None
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("pid", "assigned_gpu", "log_path", "stderr_tail", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip()
        return value


class LaunchArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    launch_id: str
    task_id: str
    experiment_id: str
    project: str
    executor: ExecutionBackend
    status: LaunchStatus = LaunchStatus.PREPARED
    frozen_spec: FrozenLaunchSpec
    code_snapshot: CodeSnapshot = Field(default_factory=CodeSnapshot)
    submission: SubmissionReceipt | None = None
    run_sh_path: str | None = None
    run_py_path: str | None = None
    env_json_path: str | None = None
    code_snapshot_path: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator(
        "launch_id",
        "task_id",
        "experiment_id",
        "project",
        "run_sh_path",
        "run_py_path",
        "env_json_path",
        "code_snapshot_path",
        mode="before",
    )
    @classmethod
    def strip_paths(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip()
        return value


class TaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    title: str
    task_kind: TaskKind
    research_role: ResearchRole
    status: TaskStatus
    latest_launch_id: str | None = None
    path: str


class ExperimentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    title: str
    parent_id: str
    status: ExperimentStatus
    assessment: ExperimentAssessment
    task_count: int = 0
    evidence_task_count: int = 0
    updated_at: str
    path: str


class ExperimentDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record: ExperimentRecord
    tasks: list[TaskSummary] = Field(default_factory=list)
    path: str
    launch_markdown: str = ""
    debrief_markdown: str = ""
    review_markdown: str = ""


class LaunchExpPhase(str, Enum):
    TASK_BREAKDOWN = "task_breakdown"
    TASK_PLANNING = "task_planning"
    SCRIPT_GENERATION = "script_generation"


class ExperimentTaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    goal: str = ""
    depends_on: list[str] = Field(default_factory=list)
    entry_hint: str = ""
    inputs: str = ""
    outputs: str = ""
    checkpoint: str = ""
    failure_modes: str = ""
    approved: bool = False

    @field_validator("id", "name", "goal", "entry_hint", "inputs", "outputs", "checkpoint", "failure_modes", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value


class LaunchExpSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    project: str
    phase: LaunchExpPhase = LaunchExpPhase.TASK_BREAKDOWN
    experiment_id: str | None = None
    task_plans: list[ExperimentTaskPlan] = Field(default_factory=list)
    current_task_index: int = 0
    run_sh_content: str = ""
    config_yaml_content: str = ""
    log_path: str = ""
    created_at: str = Field(default_factory=utc_now_iso)

    @property
    def all_tasks_approved(self) -> bool:
        return bool(self.task_plans) and all(t.approved for t in self.task_plans)

    @property
    def current_task(self) -> ExperimentTaskPlan | None:
        if 0 <= self.current_task_index < len(self.task_plans):
            return self.task_plans[self.current_task_index]
        return None

    def next_unapproved_task_index(self) -> int | None:
        for i, t in enumerate(self.task_plans):
            if not t.approved:
                return i
        return None


class HypothesisReviewSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    title: str
    current_state: str
    current_resolution: str
    suggested_state: str
    suggested_resolution: str
    supporting_experiment_ids: list[str] = Field(default_factory=list)
    contradicting_experiment_ids: list[str] = Field(default_factory=list)
    pending_experiment_ids: list[str] = Field(default_factory=list)
    reviewed_experiment_ids: list[str] = Field(default_factory=list)
    result_summary: str = ""
    decision_rationale: str = ""
    next_steps: list[str] = Field(default_factory=list)
