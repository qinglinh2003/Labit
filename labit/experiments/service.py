from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

from labit.experiments.models import (
    CodeSnapshot,
    ExecutionBackend,
    ExperimentDetail,
    ExperimentDraft,
    ExperimentAssessment,
    ExperimentExecutionProfile,
    ExperimentStatus,
    ExperimentParentType,
    ExperimentRecord,
    ExperimentSummary,
    ExperimentTaskPlan,
    FrozenLaunchSpec,
    HypothesisReviewSuggestion,
    HypothesisSnapshot,
    LaunchArtifact,
    LaunchExpPhase,
    LaunchExpSession,
    LaunchStatus,
    ResearchRole,
    SubmissionReceipt,
    TaskDraft,
    TaskKind,
    TaskRecord,
    TaskSpec,
    TaskResources,
    TaskStatus,
    TaskSummary,
    utc_now_iso,
)
from labit.hypotheses.service import HypothesisService
from labit.paths import RepoPaths
from labit.services.compute_service import ComputeService
from labit.services.project_service import ProjectService


class ExperimentService:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        project_service: ProjectService | None = None,
        compute_service: ComputeService | None = None,
        hypothesis_service: HypothesisService | None = None,
    ):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)
        self.compute_service = compute_service or ComputeService(paths)
        self.hypothesis_service = hypothesis_service or HypothesisService(paths)

    def list_experiments(self, project: str) -> list[ExperimentSummary]:
        resolved = self._require_project(project)
        experiments_dir = self.experiments_dir(resolved)
        if not experiments_dir.exists():
            return []

        summaries: list[ExperimentSummary] = []
        for path in sorted(experiments_dir.glob("e*/experiment.yaml")):
            detail = self._load_detail(path.parent)
            evidence_task_count = sum(1 for item in detail.tasks if item.research_role.value == "evidence")
            summaries.append(
                ExperimentSummary(
                    experiment_id=detail.record.experiment_id,
                    title=detail.record.title,
                    parent_id=detail.record.parent_id,
                    status=detail.record.status,
                    assessment=detail.record.assessment,
                    task_count=len(detail.tasks),
                    evidence_task_count=evidence_task_count,
                    updated_at=detail.record.updated_at,
                    path=detail.path,
                )
            )
        return sorted(summaries, key=lambda item: self._experiment_sort_key(item.experiment_id), reverse=True)

    def load_experiment(self, project: str, experiment_id: str) -> ExperimentDetail:
        resolved = self._require_project(project)
        experiment_dir = self.experiment_dir(resolved, experiment_id)
        if not (experiment_dir / "experiment.yaml").exists():
            raise FileNotFoundError(f"Experiment '{experiment_id}' not found in project '{resolved}'.")
        return self._load_detail(experiment_dir)

    def next_experiment_id(self, project: str) -> str:
        resolved = self._require_project(project)
        experiments_dir = self.experiments_dir(resolved)
        highest = 0
        if experiments_dir.exists():
            for path in experiments_dir.iterdir():
                match = re.fullmatch(r"e(\d+)", path.name)
                if not match:
                    continue
                highest = max(highest, int(match.group(1)))
        return f"e{highest + 1:03d}"

    def next_task_id(self, project: str, experiment_id: str) -> str:
        resolved = self._require_project(project)
        tasks_dir = self.tasks_dir(resolved, experiment_id)
        highest = 0
        if tasks_dir.exists():
            for path in tasks_dir.glob("t*.yaml"):
                match = re.fullmatch(r"t(\d+)\.yaml", path.name)
                if not match:
                    continue
                highest = max(highest, int(match.group(1)))
        return f"t{highest + 1:03d}"

    def create_experiment(
        self,
        *,
        project: str,
        hypothesis_id: str,
        draft: ExperimentDraft,
    ) -> ExperimentDetail:
        resolved = self._require_project(project)
        hypothesis = self.hypothesis_service.load_hypothesis(resolved, hypothesis_id).record
        experiment_id = self.next_experiment_id(resolved)
        experiment_dir = self.experiment_dir(resolved, experiment_id)
        experiment_dir.mkdir(parents=True, exist_ok=False)
        (experiment_dir / "tasks").mkdir(parents=True, exist_ok=True)
        (experiment_dir / "tasks" / "launches").mkdir(parents=True, exist_ok=True)

        record = ExperimentRecord(
            experiment_id=experiment_id,
            project=resolved,
            parent_type=ExperimentParentType.HYPOTHESIS,
            parent_id=hypothesis.hypothesis_id,
            title=draft.title,
            objective=draft.objective,
            hypothesis_snapshot=HypothesisSnapshot(
                hypothesis_id=hypothesis.hypothesis_id,
                title=hypothesis.title,
                claim=hypothesis.claim,
                success_criteria=hypothesis.success_criteria,
                failure_criteria=hypothesis.failure_criteria,
            ),
            execution=draft.execution,
            source_session_id=draft.source_session_id,
            source_paper_ids=draft.source_paper_ids or hypothesis.source_paper_ids,
        )

        evidence_task_ids: list[str] = []
        prerequisite_task_ids: list[str] = []

        for task_draft in draft.tasks:
            task_id = self.next_task_id(resolved, experiment_id)
            task_record = TaskRecord(
                task_id=task_id,
                experiment_id=experiment_id,
                project=resolved,
                title=task_draft.title,
                task_kind=task_draft.task_kind,
                research_role=task_draft.research_role,
                depends_on=task_draft.depends_on,
                spec=task_draft.spec,
                resources=task_draft.resources,
            )
            self._atomic_write_yaml(self.task_path(resolved, experiment_id, task_id), task_record.model_dump(mode="json"))
            if task_record.research_role.value == "evidence":
                evidence_task_ids.append(task_id)
            elif task_record.research_role.value == "prerequisite":
                prerequisite_task_ids.append(task_id)

        record = record.model_copy(
            update={
                "evidence_task_ids": evidence_task_ids,
                "prerequisite_task_ids": prerequisite_task_ids,
                "updated_at": utc_now_iso(),
            }
        )

        self._atomic_write_yaml(experiment_dir / "experiment.yaml", record.model_dump(mode="json"))
        self._atomic_write_text(experiment_dir / "launch.md", "")
        self._atomic_write_text(experiment_dir / "debrief.md", "")
        self._atomic_write_text(experiment_dir / "review.md", "")
        self._refresh_index(resolved)
        return self.load_experiment(resolved, experiment_id)

    def save_experiment_record(self, *, project: str, record: ExperimentRecord) -> ExperimentRecord:
        resolved = self._require_project(project)
        experiment_dir = self.experiment_dir(resolved, record.experiment_id)
        if not (experiment_dir / "experiment.yaml").exists():
            raise FileNotFoundError(f"Experiment '{record.experiment_id}' not found in project '{resolved}'.")
        updated = record.model_copy(update={"updated_at": utc_now_iso()})
        self._atomic_write_yaml(experiment_dir / "experiment.yaml", updated.model_dump(mode="json"))
        self._refresh_index(resolved)
        return updated

    def save_task_record(self, *, project: str, task: TaskRecord) -> TaskRecord:
        resolved = self._require_project(project)
        path = self.task_path(resolved, task.experiment_id, task.task_id)
        if not path.exists():
            raise FileNotFoundError(
                f"Task '{task.task_id}' not found in experiment '{task.experiment_id}' for project '{resolved}'."
            )
        updated = task.model_copy(update={"updated_at": utc_now_iso()})
        self._atomic_write_yaml(path, updated.model_dump(mode="json"))
        self.update_experiment_status(resolved, task.experiment_id)
        return updated

    def suggest_task_defaults(self, *, project: str, hypothesis_id: str) -> dict[str, str]:
        resolved = self._require_project(project)
        detail = self.hypothesis_service.load_hypothesis(resolved, hypothesis_id)
        raw = detail.raw_legacy if detail.legacy else {}
        command = self._infer_command(detail.experiment_plan_markdown, raw)
        branch = str(raw.get("branch", "")).strip()
        config_ref = str(raw.get("config", "")).strip()
        gpu = str(raw.get("gpu", "")).strip()
        output_dir = self._default_output_dir(hypothesis_id=hypothesis_id, title=detail.record.title)
        task_kind = self._infer_task_kind(command)
        return {
            "title": detail.record.title,
            "objective": detail.record.claim,
            "branch": branch,
            "config_ref": config_ref,
            "gpu": gpu,
            "command": command,
            "output_dir": output_dir,
            "task_kind": task_kind.value,
            "research_role": ResearchRole.EVIDENCE.value,
            "source_paper_ids": ",".join(detail.record.source_paper_ids),
        }

    def list_experiments_for_hypothesis(self, project: str, hypothesis_id: str) -> list[ExperimentDetail]:
        resolved = self._require_project(project)
        details: list[ExperimentDetail] = []
        for summary in self.list_experiments(resolved):
            if summary.parent_id != hypothesis_id:
                continue
            details.append(self.load_experiment(resolved, summary.experiment_id))
        return details

    def suggest_hypothesis_review(self, *, project: str, hypothesis_id: str) -> HypothesisReviewSuggestion:
        resolved = self._require_project(project)
        hypothesis_detail = self.hypothesis_service.load_hypothesis(resolved, hypothesis_id)
        experiments = self.list_experiments_for_hypothesis(resolved, hypothesis_id)

        supporting: list[str] = []
        contradicting: list[str] = []
        pending: list[str] = []
        reviewed: list[str] = []

        for experiment in experiments:
            reviewed.append(experiment.record.experiment_id)
            if experiment.record.assessment == ExperimentAssessment.SUPPORTS:
                supporting.append(experiment.record.experiment_id)
            elif experiment.record.assessment == ExperimentAssessment.CONTRADICTS:
                contradicting.append(experiment.record.experiment_id)
            elif experiment.record.status not in {
                ExperimentStatus.COMPLETED,
                ExperimentStatus.FAILED,
                ExperimentStatus.CANCELLED,
                ExperimentStatus.ANALYZED,
            }:
                pending.append(experiment.record.experiment_id)

        if supporting and not contradicting and not pending:
            suggested_state = "closed"
            suggested_resolution = "validated"
            result_summary = f"Hypothesis is currently supported by {', '.join(supporting)}."
            rationale = "All reviewed evidence-bearing experiments currently support the claim, and no contradictory or pending experiments remain."
            next_steps = ["Decide whether to archive this hypothesis or spin off a follow-up variant."]
        elif contradicting and not supporting and not pending:
            suggested_state = "closed"
            suggested_resolution = "rejected"
            result_summary = f"Hypothesis is currently contradicted by {', '.join(contradicting)}."
            rationale = "All reviewed evidence-bearing experiments currently contradict the claim, and no supporting or pending experiments remain."
            next_steps = ["Refine the hypothesis or launch a replacement experiment with a different intervention."]
        elif supporting or contradicting:
            suggested_state = "open" if pending else "closed"
            suggested_resolution = "pending" if pending else "inconclusive"
            result_summary = "Evidence is mixed across experiments."
            rationale = "At least one experiment supports the claim while another contradicts it, or some experiments are still pending."
            next_steps = ["Inspect experiment-level evidence tasks and decide whether more runs are needed."]
        elif pending:
            suggested_state = "open"
            suggested_resolution = "pending"
            result_summary = "Experiments are still in flight or not yet analyzed."
            rationale = "There is not enough completed evidence to close the hypothesis yet."
            next_steps = ["Run /debrief and wait for evidence tasks to finish before closing the hypothesis."]
        else:
            suggested_state = hypothesis_detail.record.state.value
            suggested_resolution = hypothesis_detail.record.resolution.value
            result_summary = hypothesis_detail.record.result_summary or "No reviewed experiments yet."
            rationale = "No experiment assessments are available yet."
            next_steps = ["Launch an experiment or debrief existing runs before reviewing results."]

        return HypothesisReviewSuggestion(
            hypothesis_id=hypothesis_detail.record.hypothesis_id,
            title=hypothesis_detail.record.title,
            current_state=hypothesis_detail.record.state.value,
            current_resolution=hypothesis_detail.record.resolution.value,
            suggested_state=suggested_state,
            suggested_resolution=suggested_resolution,
            supporting_experiment_ids=supporting,
            contradicting_experiment_ids=contradicting,
            pending_experiment_ids=pending,
            reviewed_experiment_ids=reviewed,
            result_summary=result_summary,
            decision_rationale=rationale,
            next_steps=next_steps,
        )

    def materialize_launch_artifact(
        self,
        *,
        project: str,
        experiment_id: str,
        task_id: str,
        run_python: str | None = None,
        code_snapshot: CodeSnapshot | None = None,
        receipt: SubmissionReceipt | None = None,
    ) -> LaunchArtifact:
        resolved = self._require_project(project)
        detail = self.load_experiment(resolved, experiment_id)
        task = self.load_task(resolved, experiment_id, task_id)
        launch_id = self._next_launch_id(resolved, experiment_id)
        launch_dir = self.launch_dir(resolved, experiment_id, launch_id)
        launch_dir.mkdir(parents=True, exist_ok=False)

        frozen_spec = FrozenLaunchSpec(
            command=self._render_command(task),
            workdir=detail.record.execution.workdir,
            output_dir=task.spec.output_dir,
            env=task.spec.env,
        )
        run_sh = self._render_run_sh(frozen_spec, detail.record.execution)
        run_sh_path = launch_dir / "run.sh"
        self._atomic_write_text(run_sh_path, run_sh)

        run_py_path: Path | None = None
        if run_python and run_python.strip():
            run_py_path = launch_dir / "run.py"
            self._atomic_write_text(run_py_path, run_python.strip() + "\n")

        env_json_path = launch_dir / "env.json"
        self._atomic_write_text(env_json_path, json.dumps(frozen_spec.env, indent=2, sort_keys=True) + "\n")

        snapshot = code_snapshot or self.build_code_snapshot(resolved, branch_hint=task.spec.branch)
        code_snapshot_path = launch_dir / "code_snapshot.json"
        self._atomic_write_text(code_snapshot_path, json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")

        artifact = LaunchArtifact(
            launch_id=launch_id,
            task_id=task.task_id,
            experiment_id=experiment_id,
            project=resolved,
            executor=detail.record.execution.backend,
            remote_user=detail.record.execution.user,
            remote_host=detail.record.execution.host,
            remote_port=detail.record.execution.port,
            ssh_key=detail.record.execution.ssh_key,
            frozen_spec=frozen_spec,
            code_snapshot=snapshot,
            submission=receipt,
            run_sh_path=str(run_sh_path.relative_to(self.paths.root)),
            run_py_path=str(run_py_path.relative_to(self.paths.root)) if run_py_path else None,
            env_json_path=str(env_json_path.relative_to(self.paths.root)),
            code_snapshot_path=str(code_snapshot_path.relative_to(self.paths.root)),
            status=self._derive_launch_status(receipt),
        )
        self._atomic_write_yaml(launch_dir / "launch.yaml", artifact.model_dump(mode="json"))
        if receipt is not None:
            self._atomic_write_yaml(launch_dir / "receipt.yaml", receipt.model_dump(mode="json"))

        task_status = task.status
        task_runtime = task.runtime
        if receipt is not None:
            if receipt.accepted:
                task_status = TaskStatus.QUEUED
            if receipt.remote_job_id is not None or receipt.pid is not None or receipt.log_path is not None:
                task_runtime = task.runtime.model_copy(
                    update={
                        "remote_job_id": receipt.remote_job_id,
                        "pid": receipt.pid,
                        "assigned_gpu": receipt.assigned_gpu,
                        "log_path": receipt.log_path,
                        "submitted_at": receipt.created_at,
                    }
                )
        updated_task = task.model_copy(
            update={
                "latest_launch_id": launch_id,
                "status": task_status,
                "runtime": task_runtime,
                "updated_at": utc_now_iso(),
            }
        )
        self._atomic_write_yaml(self.task_path(resolved, experiment_id, task_id), updated_task.model_dump(mode="json"))
        self.update_experiment_status(resolved, experiment_id)
        return artifact

    def load_task(self, project: str, experiment_id: str, task_id: str) -> TaskRecord:
        resolved = self._require_project(project)
        path = self.task_path(resolved, experiment_id, task_id)
        if not path.exists():
            raise FileNotFoundError(f"Task '{task_id}' not found in experiment '{experiment_id}'.")
        return TaskRecord.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def load_launch_artifact(self, project: str, experiment_id: str, launch_id: str) -> LaunchArtifact:
        resolved = self._require_project(project)
        path = self.launch_dir(resolved, experiment_id, launch_id) / "launch.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Launch '{launch_id}' not found in experiment '{experiment_id}'.")
        return LaunchArtifact.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def record_submission_receipt(
        self,
        *,
        project: str,
        experiment_id: str,
        launch_id: str,
        receipt: SubmissionReceipt,
    ) -> LaunchArtifact:
        resolved = self._require_project(project)
        artifact = self.load_launch_artifact(resolved, experiment_id, launch_id)
        updated_artifact = artifact.model_copy(
            update={
                "submission": receipt,
                "status": self._derive_launch_status(receipt),
                "updated_at": utc_now_iso(),
            }
        )
        launch_dir = self.launch_dir(resolved, experiment_id, launch_id)
        self._atomic_write_yaml(launch_dir / "launch.yaml", updated_artifact.model_dump(mode="json"))
        self._atomic_write_yaml(launch_dir / "receipt.yaml", receipt.model_dump(mode="json"))

        task = self.load_task(resolved, experiment_id, updated_artifact.task_id)
        task_status = task.status
        task_runtime = task.runtime
        if receipt.accepted:
            task_status = TaskStatus.QUEUED
        if receipt.remote_job_id is not None or receipt.pid is not None or receipt.log_path is not None:
            task_runtime = task.runtime.model_copy(
                update={
                    "remote_job_id": receipt.remote_job_id,
                    "pid": receipt.pid,
                    "assigned_gpu": receipt.assigned_gpu,
                    "log_path": receipt.log_path,
                    "submitted_at": receipt.created_at,
                }
            )
        updated_task = task.model_copy(
            update={
                "status": task_status,
                "runtime": task_runtime,
                "updated_at": utc_now_iso(),
            }
        )
        self._atomic_write_yaml(self.task_path(resolved, experiment_id, task.task_id), updated_task.model_dump(mode="json"))
        self.update_experiment_status(resolved, experiment_id)
        return updated_artifact

    def update_experiment_status(self, project: str, experiment_id: str) -> ExperimentRecord:
        resolved = self._require_project(project)
        detail = self.load_experiment(resolved, experiment_id)
        task_statuses = {self.load_task(resolved, experiment_id, task.task_id).status for task in detail.tasks}
        if any(status == TaskStatus.RUNNING for status in task_statuses):
            next_status = ExperimentStatus.RUNNING
        elif any(status == TaskStatus.QUEUED for status in task_statuses):
            next_status = ExperimentStatus.QUEUED
        elif task_statuses and all(status == TaskStatus.COMPLETED for status in task_statuses):
            next_status = ExperimentStatus.COMPLETED
        elif task_statuses and all(status in {TaskStatus.CANCELLED, TaskStatus.SKIPPED} for status in task_statuses):
            next_status = ExperimentStatus.CANCELLED
        elif any(status == TaskStatus.FAILED for status in task_statuses):
            next_status = ExperimentStatus.FAILED
        else:
            next_status = ExperimentStatus.PLANNED
        updated = detail.record.model_copy(update={"status": next_status, "updated_at": utc_now_iso()})
        self._atomic_write_yaml(self.experiment_dir(resolved, experiment_id) / "experiment.yaml", updated.model_dump(mode="json"))
        self._refresh_index(resolved)
        return updated

    def write_launch_markdown(self, *, project: str, experiment_id: str, content: str) -> None:
        self._write_phase_markdown(project=project, experiment_id=experiment_id, filename="launch.md", content=content)

    def write_debrief_markdown(self, *, project: str, experiment_id: str, content: str) -> None:
        self._write_phase_markdown(project=project, experiment_id=experiment_id, filename="debrief.md", content=content)

    def write_review_markdown(self, *, project: str, experiment_id: str, content: str) -> None:
        self._write_phase_markdown(project=project, experiment_id=experiment_id, filename="review.md", content=content)

    def experiments_dir(self, project: str) -> Path:
        return self.paths.vault_projects_dir / project / "experiments"

    def experiment_dir(self, project: str, experiment_id: str) -> Path:
        return self.experiments_dir(project) / experiment_id

    def tasks_dir(self, project: str, experiment_id: str) -> Path:
        return self.experiment_dir(project, experiment_id) / "tasks"

    def task_path(self, project: str, experiment_id: str, task_id: str) -> Path:
        return self.tasks_dir(project, experiment_id) / f"{task_id}.yaml"

    def launch_dir(self, project: str, experiment_id: str, launch_id: str) -> Path:
        return self.tasks_dir(project, experiment_id) / "launches" / launch_id

    def build_default_execution_profile(self, project: str) -> ExperimentExecutionProfile:
        resolved = self._require_project(project)
        spec = self.project_service.load_project(resolved)
        compute_name = spec.compute_profile.strip()
        if not compute_name:
            raise ValueError(
                f"Project '{resolved}' is not connected to a compute profile. Run 'labit project edit {resolved}' to attach one."
            )
        compute = self.compute_service.load_compute(compute_name)
        return ExperimentExecutionProfile(
            backend=ExecutionBackend.SSH,
            profile=compute.name,
            user=compute.connection.user,
            host=compute.connection.host,
            port=compute.connection.port,
            ssh_key=compute.connection.ssh_key or "",
            workdir=compute.workspace.workdir,
            datadir=compute.workspace.datadir or "",
            setup_script=compute.setup.script,
        )

    def build_code_snapshot(self, project: str, *, branch_hint: str = "") -> CodeSnapshot:
        resolved = self._require_project(project)
        code_dir = self.paths.vault_projects_dir / resolved / "code"
        repo = ""
        branch = branch_hint.strip()
        commit = ""
        dirty = False

        if code_dir.exists():
            repo = self._git_output(code_dir, ["config", "--get", "remote.origin.url"]) or ""
            branch = branch or self._git_output(code_dir, ["rev-parse", "--abbrev-ref", "HEAD"]) or ""
            commit = self._git_output(code_dir, ["rev-parse", "HEAD"]) or ""
            dirty = bool(self._git_output(code_dir, ["status", "--porcelain"]))

        return CodeSnapshot(repo=repo, branch=branch, commit=commit, dirty=dirty)

    # ── Launch-exp planning session ──

    def start_launch_exp_session(
        self,
        *,
        project: str,
        hypothesis_id: str,
    ) -> LaunchExpSession:
        resolved = self._require_project(project)
        # Verify hypothesis exists
        self.hypothesis_service.load_hypothesis(resolved, hypothesis_id)

        experiment_id = self.next_experiment_id(resolved)
        experiment_dir = self.experiment_dir(resolved, experiment_id)
        experiment_dir.mkdir(parents=True, exist_ok=True)

        log_dir = experiment_dir / ".sessions"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str((log_dir / "planning.jsonl").relative_to(self.paths.root))

        session = LaunchExpSession(
            hypothesis_id=hypothesis_id,
            project=resolved,
            experiment_id=experiment_id,
            log_path=log_path,
        )
        self._log_planning_event(session, "session_started", {
            "hypothesis_id": hypothesis_id,
            "experiment_id": experiment_id,
        })
        return session

    def save_task_plans(self, session: LaunchExpSession, tasks: list[ExperimentTaskPlan]) -> LaunchExpSession:
        session = session.model_copy(update={"task_plans": tasks})
        self._log_planning_event(session, "task_breakdown_updated", {
            "task_count": len(tasks),
            "tasks": [{"id": t.id, "name": t.name} for t in tasks],
        })
        return session

    def approve_task_list(self, session: LaunchExpSession) -> LaunchExpSession:
        """Move from task_breakdown to task_planning phase."""
        first_idx = session.next_unapproved_task_index()
        session = session.model_copy(update={
            "phase": LaunchExpPhase.TASK_PLANNING,
            "current_task_index": first_idx if first_idx is not None else 0,
        })
        self._log_planning_event(session, "task_list_approved", {
            "task_count": len(session.task_plans),
        })
        return session

    def update_task_detail(self, session: LaunchExpSession, task: ExperimentTaskPlan) -> LaunchExpSession:
        tasks = list(session.task_plans)
        for i, t in enumerate(tasks):
            if t.id == task.id:
                tasks[i] = task
                break
        return session.model_copy(update={"task_plans": tasks})

    def approve_task(self, session: LaunchExpSession, task_id: str) -> LaunchExpSession:
        tasks = list(session.task_plans)
        for i, t in enumerate(tasks):
            if t.id == task_id:
                tasks[i] = t.model_copy(update={"approved": True})
                break
        session = session.model_copy(update={"task_plans": tasks})

        self._log_planning_event(session, "task_approved", {"task_id": task_id})

        # Advance to next unapproved task, or move to script generation
        next_idx = session.next_unapproved_task_index()
        if next_idx is not None:
            session = session.model_copy(update={"current_task_index": next_idx})
        elif session.all_tasks_approved:
            session = session.model_copy(update={
                "phase": LaunchExpPhase.SCRIPT_GENERATION,
            })
            self._log_planning_event(session, "all_tasks_approved", {})
        return session

    def reopen_task(self, session: LaunchExpSession, task_id: str) -> LaunchExpSession:
        tasks = list(session.task_plans)
        for i, t in enumerate(tasks):
            if t.id == task_id:
                tasks[i] = t.model_copy(update={"approved": False})
                session = session.model_copy(update={
                    "task_plans": tasks,
                    "phase": LaunchExpPhase.TASK_PLANNING,
                    "current_task_index": i,
                })
                self._log_planning_event(session, "task_reopened", {"task_id": task_id})
                return session
        raise ValueError(f"Task '{task_id}' not found in session.")

    def save_script(self, session: LaunchExpSession, run_sh: str, config_yaml: str) -> LaunchExpSession:
        resolved = session.project
        experiment_dir = self.experiment_dir(resolved, session.experiment_id)

        # Write run.sh
        run_sh_path = experiment_dir / "run.sh"
        self._atomic_write_text(run_sh_path, run_sh)

        # Write config.yaml if provided
        if config_yaml.strip():
            self._atomic_write_text(experiment_dir / "config.yaml", config_yaml)

        session = session.model_copy(update={
            "run_sh_content": run_sh,
            "config_yaml_content": config_yaml,
        })
        self._log_planning_event(session, "script_generated", {
            "run_sh_lines": len(run_sh.splitlines()),
        })
        return session

    def finalize_experiment(self, session: LaunchExpSession) -> ExperimentDetail:
        """Create the experiment record and task records from the planning session."""
        resolved = session.project
        hypothesis = self.hypothesis_service.load_hypothesis(resolved, session.hypothesis_id).record
        experiment_id = session.experiment_id
        experiment_dir = self.experiment_dir(resolved, experiment_id)
        (experiment_dir / "tasks").mkdir(parents=True, exist_ok=True)

        record = ExperimentRecord(
            experiment_id=experiment_id,
            project=resolved,
            parent_type=ExperimentParentType.HYPOTHESIS,
            parent_id=hypothesis.hypothesis_id,
            title=hypothesis.title,
            objective=hypothesis.claim,
            hypothesis_snapshot=HypothesisSnapshot(
                hypothesis_id=hypothesis.hypothesis_id,
                title=hypothesis.title,
                claim=hypothesis.claim,
                success_criteria=hypothesis.success_criteria,
                failure_criteria=hypothesis.failure_criteria,
            ),
            execution=self.build_default_execution_profile(resolved),
        )

        evidence_task_ids: list[str] = []
        for task_plan in session.task_plans:
            task_record = TaskRecord(
                task_id=task_plan.id,
                experiment_id=experiment_id,
                project=resolved,
                title=task_plan.name,
                task_kind=TaskKind.CUSTOM,
                research_role=ResearchRole.EVIDENCE,
                depends_on=task_plan.depends_on,
                spec=TaskSpec(
                    command=f"# See run.sh — task {task_plan.id}: {task_plan.name}",
                    entrypoint=task_plan.entry_hint,
                ),
            )
            self._atomic_write_yaml(
                self.task_path(resolved, experiment_id, task_plan.id),
                task_record.model_dump(mode="json"),
            )
            evidence_task_ids.append(task_plan.id)

        record = record.model_copy(update={
            "evidence_task_ids": evidence_task_ids,
            "updated_at": utc_now_iso(),
        })
        self._atomic_write_yaml(experiment_dir / "experiment.yaml", record.model_dump(mode="json"))

        # Write experiment_plan.md from task plans
        plan_lines = [f"# Experiment Plan: {hypothesis.title}\n"]
        for t in session.task_plans:
            plan_lines.append(f"## {t.id}: {t.name}")
            plan_lines.append(f"**Goal**: {t.goal}")
            if t.depends_on:
                plan_lines.append(f"**Depends on**: {', '.join(t.depends_on)}")
            if t.entry_hint:
                plan_lines.append(f"**Entry**: {t.entry_hint}")
            if t.inputs:
                plan_lines.append(f"**Inputs**: {t.inputs}")
            if t.outputs:
                plan_lines.append(f"**Outputs**: {t.outputs}")
            if t.checkpoint:
                plan_lines.append(f"**Checkpoint**: {t.checkpoint}")
            if t.failure_modes:
                plan_lines.append(f"**Failure modes**: {t.failure_modes}")
            plan_lines.append("")
        self._atomic_write_text(experiment_dir / "experiment_plan.md", "\n".join(plan_lines))
        self._atomic_write_text(experiment_dir / "launch.md", "")
        self._atomic_write_text(experiment_dir / "debrief.md", "")
        self._atomic_write_text(experiment_dir / "review.md", "")

        self._log_planning_event(session, "experiment_finalized", {
            "experiment_id": experiment_id,
        })
        self._refresh_index(resolved)
        return self.load_experiment(resolved, experiment_id)

    def validate_dependency_graph(self, tasks: list[ExperimentTaskPlan]) -> str | None:
        """Check for circular dependencies. Returns error message or None."""
        task_ids = {t.id for t in tasks}
        # Check for unknown dependencies
        for t in tasks:
            for dep in t.depends_on:
                if dep not in task_ids:
                    return f"Task '{t.id}' depends on unknown task '{dep}'."
        # Topological sort to detect cycles
        visited: set[str] = set()
        in_stack: set[str] = set()
        deps_map = {t.id: t.depends_on for t in tasks}

        def dfs(node: str) -> str | None:
            if node in in_stack:
                return f"Circular dependency detected involving task '{node}'."
            if node in visited:
                return None
            in_stack.add(node)
            for dep in deps_map.get(node, []):
                err = dfs(dep)
                if err:
                    return err
            in_stack.discard(node)
            visited.add(node)
            return None

        for tid in task_ids:
            err = dfs(tid)
            if err:
                return err
        return None

    def planning_interaction_excerpt(self, session: LaunchExpSession, last_n: int = 10) -> str:
        log_path = self.paths.root / session.log_path
        if not log_path.exists():
            return "(no interaction log)"
        lines: list[str] = []
        for raw_line in log_path.read_text(encoding="utf-8").strip().splitlines():
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            lines.append(f"[{event.get('type', '?')}] {event.get('summary', json.dumps(event.get('payload', {})))}")
        return "\n".join(lines[-last_n:]) if lines else "(empty)"

    def get_code_tree(self, project: str, max_depth: int = 3) -> str:
        resolved = self._require_project(project)
        code_dir = self.paths.vault_projects_dir / resolved / "code"
        if not code_dir.exists():
            return "(no code directory found)"
        lines: list[str] = []
        self._tree(code_dir, code_dir, lines, depth=0, max_depth=max_depth)
        return "\n".join(lines) if lines else "(empty code directory)"

    def _tree(self, base: Path, current: Path, lines: list[str], depth: int, max_depth: int) -> None:
        if depth >= max_depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except PermissionError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue
            rel = entry.relative_to(base)
            indent = "  " * depth
            if entry.is_dir():
                lines.append(f"{indent}{rel}/")
                self._tree(base, entry, lines, depth + 1, max_depth)
            else:
                lines.append(f"{indent}{rel}")

    def _log_planning_event(self, session: LaunchExpSession, event_type: str, payload: dict) -> None:
        log_path = self.paths.root / session.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "type": event_type,
            "timestamp": utc_now_iso(),
            "payload": payload,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_user_instruction(self, session: LaunchExpSession, instruction: str) -> None:
        self._log_planning_event(session, "user_instruction", {
            "content": instruction,
            "phase": session.phase.value,
        })

    def log_agent_revision(self, session: LaunchExpSession, summary: str, provider: str = "") -> None:
        self._log_planning_event(session, "agent_revision", {
            "summary": summary,
            "provider": provider,
            "phase": session.phase.value,
        })

    def _load_detail(self, experiment_dir: Path) -> ExperimentDetail:
        record = ExperimentRecord.model_validate(
            yaml.safe_load((experiment_dir / "experiment.yaml").read_text(encoding="utf-8")) or {}
        )
        task_summaries: list[TaskSummary] = []
        for task_path in sorted((experiment_dir / "tasks").glob("t*.yaml")):
            task = TaskRecord.model_validate(yaml.safe_load(task_path.read_text(encoding="utf-8")) or {})
            task_summaries.append(
                TaskSummary(
                    task_id=task.task_id,
                    title=task.title,
                    task_kind=task.task_kind,
                    research_role=task.research_role,
                    status=task.status,
                    latest_launch_id=task.latest_launch_id,
                    path=str(task_path.relative_to(self.paths.root)),
                )
            )

        return ExperimentDetail(
            record=record,
            tasks=task_summaries,
            path=str(experiment_dir.relative_to(self.paths.root)),
            launch_markdown=self._safe_read(experiment_dir / "launch.md"),
            debrief_markdown=self._safe_read(experiment_dir / "debrief.md"),
            review_markdown=self._safe_read(experiment_dir / "review.md"),
        )

    def _write_phase_markdown(self, *, project: str, experiment_id: str, filename: str, content: str) -> None:
        resolved = self._require_project(project)
        experiment_dir = self.experiment_dir(resolved, experiment_id)
        if not (experiment_dir / "experiment.yaml").exists():
            raise FileNotFoundError(f"Experiment '{experiment_id}' not found in project '{resolved}'.")
        self._atomic_write_text(experiment_dir / filename, content.strip() + "\n")

    def _require_project(self, project: str) -> str:
        resolved = self.project_service.resolve_project_name(project)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{project}' not found. Available projects: {', '.join(self.project_service.list_project_names()) or '(none)'}"
            )
        return resolved

    def _refresh_index(self, project: str) -> None:
        summaries = self.list_experiments(project)
        payload = {
            "project": project,
            "count": len(summaries),
            "experiments": [item.model_dump(mode="json") for item in summaries],
            "updated_at": utc_now_iso(),
        }
        self._atomic_write_yaml(self.experiments_dir(project) / "index.yaml", payload)

    def _render_command(self, task: TaskRecord) -> str:
        if task.spec.command:
            return task.spec.command
        parts = ["python", task.spec.entrypoint]
        for key, value in task.spec.args.items():
            option = f"--{str(key).replace('_', '-')}"
            if isinstance(value, bool):
                if value:
                    parts.append(option)
                continue
            if value is None:
                continue
            parts.extend([option, str(value)])
        return " ".join(part for part in parts if part)

    def _infer_command(self, experiment_plan_markdown: str, raw_legacy: dict[str, Any]) -> str:
        for candidate in self._command_candidates(experiment_plan_markdown, raw_legacy):
            if candidate:
                return candidate
        return ""

    def _command_candidates(self, experiment_plan_markdown: str, raw_legacy: dict[str, Any]) -> Iterable[str]:
        direct = str(raw_legacy.get("command", "")).strip()
        if direct:
            yield direct
        patterns = (
            r"`((?:python|bash|uv run|nohup)[^`]+)`",
            r"^\s*-\s*`((?:python|bash|uv run|nohup)[^`]+)`",
            r"^\s*-\s*((?:python|bash|uv run|nohup)\s.+)$",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, experiment_plan_markdown, flags=re.IGNORECASE | re.MULTILINE):
                command = " ".join(match.group(1).split()).strip()
                if command:
                    yield command
        config_ref = str(raw_legacy.get("config", "")).strip()
        if config_ref:
            yield f"python scripts/eval/run_eval.py --config {config_ref}"

    def _default_output_dir(self, *, hypothesis_id: str, title: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", title.strip().lower()).strip("-")
        slug = slug or hypothesis_id.lower()
        return f"outputs/experiments/{hypothesis_id}/{slug}"

    def _infer_task_kind(self, command: str) -> TaskKind:
        lowered = command.lower()
        if "extract" in lowered:
            return TaskKind.EXTRACT
        if "train" in lowered:
            return TaskKind.TRAIN
        if "eval" in lowered:
            return TaskKind.EVAL
        if "sync" in lowered:
            return TaskKind.SYNC
        if "analysis" in lowered or "analyze" in lowered:
            return TaskKind.ANALYSIS
        if "prep" in lowered or "prepare" in lowered:
            return TaskKind.DATA_PREP
        return TaskKind.CUSTOM

    def _render_run_sh(self, spec: FrozenLaunchSpec, execution: ExperimentExecutionProfile) -> str:
        lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
        lines.extend(self._render_runtime_preamble(execution))
        for key, value in spec.env.items():
            escaped = value.replace('"', '\\"')
            lines.append(f'export {key}="{escaped}"')
        lines.append(spec.command)
        return "\n".join(lines).rstrip() + "\n"

    def _next_launch_id(self, project: str, experiment_id: str) -> str:
        launches_dir = self.tasks_dir(project, experiment_id) / "launches"
        highest = 0
        if launches_dir.exists():
            for path in launches_dir.iterdir():
                match = re.fullmatch(r"l(\d+)", path.name)
                if not match:
                    continue
                highest = max(highest, int(match.group(1)))
        return f"l{highest + 1:03d}"

    def _derive_launch_status(self, receipt: SubmissionReceipt | None):
        if receipt is None:
            return LaunchStatus.PREPARED
        if receipt.accepted:
            return LaunchStatus.SUBMITTED
        if receipt.error_kind and receipt.error_kind.value == "task_spec_error":
            return LaunchStatus.REJECTED
        return LaunchStatus.FAILED

    def _experiment_sort_key(self, experiment_id: str) -> tuple[int, str]:
        match = re.fullmatch(r"e(\d+)", experiment_id)
        if match:
            return int(match.group(1)), experiment_id
        return 0, experiment_id

    def _safe_read(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def _render_runtime_preamble(self, execution: ExperimentExecutionProfile) -> list[str]:
        lines: list[str] = []
        if execution.setup_script:
            lines.extend(execution.setup_script.splitlines())
        if execution.workdir:
            lines.append(f'cd "{self._shell_path(execution.workdir)}"')
        if execution.datadir:
            escaped = self._shell_path(execution.datadir).replace('"', '\\"')
            lines.append(f'export LABIT_DATA_DIR="{escaped}"')
        return lines

    def _shell_path(self, value: str) -> str:
        if value == "~":
            return "$HOME"
        if value.startswith("~/"):
            return f"$HOME/{value[2:]}"
        return value

    def _git_output(self, cwd: Path, args: list[str]) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        output = (result.stdout or "").strip()
        return output or None

    def _atomic_write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)

    def _atomic_write_yaml(self, path: Path, payload: dict[str, Any]) -> None:
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        self._atomic_write_text(path, text)
