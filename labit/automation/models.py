from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from labit.agents.models import ProviderKind


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class AutoSessionStatus(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"
    NEEDS_HUMAN = "needs_human"
    DONE = "done"
    STOPPED = "stopped"


class AutoAction(str, Enum):
    WAIT = "wait"
    ACT = "act"
    DELIBERATE = "deliberate"
    DONE = "done"
    NEEDS_HUMAN = "needs_human"


class AutoActor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    provider: ProviderKind


class AutoSessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    constraint: str
    success_criteria: str
    design_doc: str = ""
    supervisor_agent: str = "codex"
    status: AutoSessionStatus = AutoSessionStatus.RUNNING
    max_iterations: int = 8
    poll_seconds: int = 120
    current_iteration: int = 0
    experiment_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    last_observation_summary: str = ""
    last_decision_summary: str = ""


class WorkerTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    title: str
    instructions: str


class ExperimentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    title: str
    status: str
    assessment: str
    task_status_counts: dict[str, int] = Field(default_factory=dict)
    latest_runtime_status: str = ""
    latest_signal: str = ""
    results_available: bool = False


class AutoObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str
    summary: str
    experiments: list[ExperimentObservation] = Field(default_factory=list)
    has_running: bool = False
    has_failures: bool = False
    has_results: bool = False
    generated_at: str = Field(default_factory=utc_now_iso)


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str
    status: str
    summary: str
    actions_taken: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    follow_up: str = ""


class DiscussionNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str
    summary: str
    evidence: list[str] = Field(default_factory=list)
    next_step: str = ""


class AutoIterationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int
    trigger: str
    observation_summary: str
    action: AutoAction
    decision_summary: str
    worker_tasks: list[WorkerTask] = Field(default_factory=list)
    worker_results: list[WorkerResult] = Field(default_factory=list)
    discussion: list[DiscussionNote] = Field(default_factory=list)
    human_needed: bool = False
    success_reached: bool = False
    created_at: str = Field(default_factory=utc_now_iso)
