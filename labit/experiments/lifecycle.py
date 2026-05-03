from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from labit.experiments.models import ExperimentStatus, TaskStatus


class ExperimentLifecycleState(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ANALYZED = "analyzed"
    UNKNOWN = "unknown"


TERMINAL_EXPERIMENT_STATES = {
    ExperimentLifecycleState.COMPLETED,
    ExperimentLifecycleState.FAILED,
    ExperimentLifecycleState.CANCELLED,
    ExperimentLifecycleState.ANALYZED,
}


class ExperimentLifecycleSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    path: str
    state: ExperimentLifecycleState
    has_record: bool
    has_planning_log: bool
    has_run_sh: bool
    has_task_records: bool

    @property
    def is_finalized(self) -> bool:
        return self.has_record

    @property
    def has_launch_exp_artifacts(self) -> bool:
        return self.has_record or self.has_planning_log or self.has_run_sh


def summarize_task_statuses(task_statuses: set[TaskStatus]) -> ExperimentStatus:
    """Return the experiment status implied by its task statuses."""
    if any(status == TaskStatus.RUNNING for status in task_statuses):
        return ExperimentStatus.RUNNING
    if any(status == TaskStatus.QUEUED for status in task_statuses):
        return ExperimentStatus.QUEUED
    if task_statuses and all(status == TaskStatus.COMPLETED for status in task_statuses):
        return ExperimentStatus.COMPLETED
    if task_statuses and all(status in {TaskStatus.CANCELLED, TaskStatus.SKIPPED} for status in task_statuses):
        return ExperimentStatus.CANCELLED
    if any(status == TaskStatus.FAILED for status in task_statuses):
        return ExperimentStatus.FAILED
    return ExperimentStatus.PLANNED


def lifecycle_from_status(status: ExperimentStatus) -> ExperimentLifecycleState:
    try:
        return ExperimentLifecycleState(status.value)
    except ValueError:
        return ExperimentLifecycleState.UNKNOWN


def inspect_experiment_dir(experiment_dir: Path) -> ExperimentLifecycleSnapshot:
    """Classify an experiment directory without requiring finalized metadata."""
    has_record = (experiment_dir / "experiment.yaml").exists()
    has_planning_log = (experiment_dir / ".sessions" / "planning.jsonl").exists()
    has_run_sh = (experiment_dir / "run.sh").exists()
    tasks_dir = experiment_dir / "tasks"
    has_task_records = tasks_dir.exists() and any(tasks_dir.glob("t*.yaml"))

    if has_record:
        state = ExperimentLifecycleState.PLANNED
    elif has_planning_log or has_run_sh or has_task_records:
        state = ExperimentLifecycleState.DRAFT
    else:
        state = ExperimentLifecycleState.UNKNOWN

    return ExperimentLifecycleSnapshot(
        experiment_id=experiment_dir.name,
        path=str(experiment_dir),
        state=state,
        has_record=has_record,
        has_planning_log=has_planning_log,
        has_run_sh=has_run_sh,
        has_task_records=has_task_records,
    )
