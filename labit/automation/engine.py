from __future__ import annotations

import textwrap
from pathlib import Path
import subprocess

from labit.agents.models import (
    AgentRequest,
    AgentRole,
    CodeSnapshot,
    CollaborationMode,
    ContextPack,
    MemorySnapshot,
    ProjectSnapshot,
    ProviderAssignment,
    ProviderKind,
    TaskSpec,
    WorkspaceSnapshot,
)
from labit.agents.adapters.base import AgentAdapterError
from labit.agents.orchestrator import AgentRuntime
from labit.automation.models import (
    AutoAction,
    AutoActor,
    AutoIterationEntry,
    AutoObservation,
    AutoSessionRecord,
    AutoSessionStatus,
    DiscussionNote,
    WorkerResult,
    WorkerTask,
)
from labit.automation.observer import AutomationObserver
from labit.automation.store import AutomationStore
from labit.experiments.service import ExperimentService
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


class AutoIterationEngine:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        project_service: ProjectService | None = None,
        experiment_service: ExperimentService | None = None,
        store: AutomationStore | None = None,
        runtime: AgentRuntime | None = None,
    ):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)
        self.experiment_service = experiment_service or ExperimentService(paths)
        self.store = store or AutomationStore(paths)
        self.runtime = runtime or AgentRuntime(paths)
        self.observer = AutomationObserver(paths, self.experiment_service)

    def start_session(
        self,
        *,
        project: str,
        constraint: str,
        success_criteria: str,
        design_doc: str = "",
        max_iterations: int = 8,
        poll_seconds: int = 120,
        experiment_ids: list[str] | None = None,
    ) -> AutoSessionRecord:
        session = AutoSessionRecord(
            project=project,
            constraint=constraint.strip(),
            success_criteria=success_criteria.strip(),
            design_doc=design_doc.strip(),
            max_iterations=max_iterations,
            poll_seconds=poll_seconds,
            experiment_ids=experiment_ids or [],
        )
        return self.store.save_session(session)

    def stop_session(self, project: str) -> AutoSessionRecord:
        session = self._require_session(project)
        updated = session.model_copy(update={"status": AutoSessionStatus.STOPPED})
        return self.store.save_session(updated)

    def status(self, project: str) -> tuple[AutoSessionRecord | None, list[AutoIterationEntry]]:
        return self.store.load_session(project), self.store.recent_iterations(project, limit=5)

    def run_iteration(self, *, project: str, actors: list[AutoActor]) -> AutoIterationEntry:
        session = self._require_session(project)
        observation = self.observer.observe(project=project, experiment_ids=session.experiment_ids)

        # If nothing changed since last observation, skip the LLM call entirely
        if self._observation_unchanged(session, observation):
            entry = AutoIterationEntry(
                iteration=session.current_iteration + 1,
                trigger="no_change",
                observation_summary=observation.summary,
                action=AutoAction.WAIT,
                decision_summary="No change since last observation. Waiting for backend events.",
                human_needed=False,
                success_reached=False,
            )
            self.store.append_iteration(project, entry)
            updated = session.model_copy(
                update={
                    "current_iteration": entry.iteration,
                    "status": AutoSessionStatus.WAITING,
                    "last_observation_summary": observation.summary,
                    "last_decision_summary": entry.decision_summary,
                }
            )
            self.store.save_session(updated)
            self._write_snapshot(updated, entry)
            return entry

        try:
            decision = self._supervisor_decide(session, observation, actors)
        except (AgentAdapterError, Exception) as exc:
            decision = {
                "action": AutoAction.WAIT.value,
                "summary": f"Supervisor failed: {exc}",
                "worker_tasks": [],
                "human_needed": False,
                "success_reached": False,
            }

        worker_results: list[WorkerResult] = []
        discussion: list[DiscussionNote] = []

        if decision["action"] == AutoAction.ACT.value:
            worker_results = self._execute_worker_tasks(
                session=session,
                observation=observation,
                actors=actors,
                tasks=decision["worker_tasks"],
            )
        elif decision["action"] == AutoAction.DELIBERATE.value:
            discussion = self._deliberate(session=session, observation=observation, actors=actors)

        action = AutoAction(decision["action"])
        entry = AutoIterationEntry(
            iteration=session.current_iteration + 1,
            trigger=self._trigger_from_observation(observation),
            observation_summary=observation.summary,
            action=action,
            decision_summary=decision["summary"],
            worker_tasks=[WorkerTask.model_validate(item) for item in decision["worker_tasks"]],
            worker_results=worker_results,
            discussion=discussion,
            human_needed=decision["human_needed"],
            success_reached=decision["success_reached"],
        )
        self.store.append_iteration(project, entry)

        next_status = AutoSessionStatus.RUNNING
        if action == AutoAction.WAIT:
            next_status = AutoSessionStatus.WAITING
        elif action == AutoAction.NEEDS_HUMAN:
            next_status = AutoSessionStatus.NEEDS_HUMAN
        elif action == AutoAction.DONE:
            next_status = AutoSessionStatus.DONE

        updated = session.model_copy(
            update={
                "current_iteration": entry.iteration,
                "status": next_status,
                "last_observation_summary": observation.summary,
                "last_decision_summary": decision["summary"],
            }
        )
        self.store.save_session(updated)
        self._write_snapshot(updated, entry)
        return entry

    def _write_snapshot(self, session: AutoSessionRecord, latest_entry: AutoIterationEntry) -> None:
        recent = self.store.recent_iterations(session.project, limit=5)
        lines = [
            f"# Auto-Iteration Snapshot — {session.project}",
            "",
            f"**Status**: {session.status.value}  ",
            f"**Iteration**: {session.current_iteration}/{session.max_iterations}  ",
            f"**Supervisor**: {session.supervisor_agent}  ",
            f"**Updated**: {session.updated_at}",
            "",
            "## Goal",
            "",
            f"**Constraint**: {session.constraint}  ",
            f"**Success criteria**: {session.success_criteria}",
            "",
        ]
        if session.design_doc:
            lines += ["## Design Document", "", session.design_doc[:2000], ""]

        lines += ["## Latest Iteration", ""]
        lines += self._render_iteration_md(latest_entry)
        lines.append("")

        if len(recent) > 1:
            lines += ["## Recent Iterations", ""]
            for entry in recent[:-1]:
                lines.append(f"### Iteration {entry.iteration}")
                lines += self._render_iteration_md(entry)
                lines.append("")

        self.store.save_snapshot(session.project, "\n".join(lines))

    def _render_iteration_md(self, entry: AutoIterationEntry) -> list[str]:
        lines = [
            f"- **Trigger**: {entry.trigger}",
            f"- **Action**: {entry.action.value}",
            f"- **Decision**: {entry.decision_summary}",
        ]
        if entry.worker_tasks:
            lines.append("- **Worker Tasks**:")
            for wt in entry.worker_tasks:
                lines.append(f"  - {wt.worker}: {wt.title}")
        if entry.worker_results:
            lines.append("- **Worker Results**:")
            for wr in entry.worker_results:
                lines.append(f"  - {wr.worker} [{wr.status}]: {wr.summary[:200]}")
                if wr.actions_taken:
                    for action in wr.actions_taken[:5]:
                        lines.append(f"    - {action}")
                if wr.follow_up:
                    lines.append(f"    - Follow-up: {wr.follow_up}")
        if entry.discussion:
            lines.append("- **Discussion**:")
            for note in entry.discussion:
                lines.append(f"  - {note.actor}: {note.summary[:200]}")
                if note.evidence:
                    for ev in note.evidence[:3]:
                        lines.append(f"    - Evidence: {ev}")
                if note.next_step:
                    lines.append(f"    - Next: {note.next_step}")
        if entry.human_needed:
            lines.append("- **NEEDS HUMAN INTERVENTION**")
        if entry.success_reached:
            lines.append("- **SUCCESS CRITERIA MET**")
        return lines

    def _require_session(self, project: str) -> AutoSessionRecord:
        session = self.store.load_session(project)
        if session is None:
            raise FileNotFoundError(f"No active auto session for project '{project}'.")
        return session

    def _project_code_dir(self, project: str) -> Path:
        return self.paths.vault_projects_dir / project / "code"

    def _context_pack(self, *, project: str, goal: str, mode: CollaborationMode) -> ContextPack:
        spec = self.project_service.load_project(project)
        code_dir = self._project_code_dir(project)
        code_snapshot = self.experiment_service.build_code_snapshot(project)
        return ContextPack(
            project=ProjectSnapshot(
                name=project,
                description=spec.description,
                keywords=list(spec.keywords),
                relevance_criteria=spec.relevance_criteria,
            ),
            task=TaskSpec(
                kind="auto_iteration",
                goal=goal,
                mode=mode,
                requires_mutation=True,
                write_scope=[str(code_dir)],
            ),
            memory=MemorySnapshot(),
            code=CodeSnapshot(
                project_code_dir=str(code_dir),
                notes=[
                    f"repo={code_snapshot.repo}",
                    f"branch={code_snapshot.branch}",
                    f"commit={code_snapshot.commit}",
                    f"dirty={str(code_snapshot.dirty).lower()}",
                ],
            ),
            workspace=WorkspaceSnapshot(
                repo_root=str(self.paths.root),
                allowed_write_scope=[str(code_dir)],
            ),
            extras={},
        )

    def _supervisor_decide(self, session: AutoSessionRecord, observation: AutoObservation, actors: list[AutoActor]) -> dict:
        supervisor = actors[0]
        recent = self.store.recent_iterations(session.project, limit=3)
        history_lines: list[str] = []
        prior_task_titles: list[str] = []
        for item in recent:
            history_lines.append(f"- iter {item.iteration}: {item.action.value} | {item.decision_summary}")
            for wt in item.worker_tasks:
                prior_task_titles.append(wt.title)
        history = "\n".join(history_lines) or "(none)"
        prior_tasks_block = ""
        if prior_task_titles:
            prior_tasks_block = (
                "\nTasks already completed in prior iterations (DO NOT repeat these):\n"
                + "\n".join(f"- {title}" for title in prior_task_titles[-10:])
                + "\n"
            )
        design_block = ""
        if session.design_doc:
            design_block = f"\nDesign document:\n{session.design_doc}\n"
        prompt = textwrap.dedent(
            f"""
            You are the supervisor in Labit's auto-iteration loop.
            Your job is to decide the next smallest useful step, not to dominate scientific judgment.
            {design_block}
            Constraint:
            {session.constraint}

            Success criteria:
            {session.success_criteria}

            Observation:
            {observation.summary}

            Recent iteration log:
            {history}
            {prior_tasks_block}
            Available workers:
            - {actors[1].name}
            - {actors[2].name}

            CRITICAL RULES:
            1. If all experiments are queued/pending with no new results, failures, or status changes, you MUST output "wait". Do NOT invent exploratory side-tasks just to keep workers busy.
            2. Do NOT assign tasks that are substantially similar to ones already completed in prior iterations.
            3. Only output "act" when there is a concrete, new event that requires worker action (e.g. a failure to fix, new results to analyze, a blocker to resolve).
            4. Output "deliberate" only when new experiment results are available.

            Decide one of:
            - wait: no useful action yet (DEFAULT when nothing has changed)
            - act: assign tasks to workers (only when there's a new event requiring action)
            - deliberate: results are ready and the three agents should discuss them
            - needs_human: stop and ask the user
            - done: success criteria already met

            Keep worker tasks concrete, short, and bounded to the current project.
            """
        ).strip()
        schema = self._supervisor_schema()
        manifest = self.runtime.begin_run(
            self._context_pack(project=session.project, goal="Decide next auto-iteration step", mode=CollaborationMode.DISCUSSION),
            assignments=[ProviderAssignment(role=AgentRole.DISCUSSANT, provider=supervisor.provider)],
        )
        artifact = self.runtime.run_role(
            manifest,
            role=AgentRole.DISCUSSANT,
            provider=supervisor.provider,
            request=AgentRequest(
                role=AgentRole.DISCUSSANT,
                prompt=prompt,
                output_schema=schema,
                cwd=str(self._project_code_dir(session.project)),
            ),
        )
        self.runtime.finish_run(manifest)
        payload = artifact.output if isinstance(artifact.output, dict) else {}
        return {
            "action": str(payload.get("action", AutoAction.WAIT.value)),
            "summary": str(payload.get("summary", "")).strip(),
            "worker_tasks": payload.get("worker_tasks", []) or [],
            "human_needed": bool(payload.get("human_needed", False)),
            "success_reached": bool(payload.get("success_reached", False)),
        }

    def _execute_worker_tasks(
        self,
        *,
        session: AutoSessionRecord,
        observation: AutoObservation,
        actors: list[AutoActor],
        tasks: list[dict],
    ) -> list[WorkerResult]:
        actor_by_name = {item.name: item for item in actors[1:]}
        results: list[WorkerResult] = []
        code_dir = self._project_code_dir(session.project)
        for task_payload in tasks[:2]:
            task = WorkerTask.model_validate(task_payload)
            actor = actor_by_name.get(task.worker)
            if actor is None:
                continue
            before_status = self._git_status_lines(code_dir)
            prompt = textwrap.dedent(
                f"""
                You are {actor.name} in Labit's auto-iteration loop.
                Work directly in the project codebase when needed.
                You should actually perform the task, including code edits and shell commands when useful.
                Do not only describe a plan.

                IMPORTANT: If your task involves launching or running experiments, submit the job and return immediately.
                Do NOT wait for experiments to finish. Labit's backend will monitor progress and collect results automatically.
                Your job is to kick things off (or fix/patch code), then exit cleanly.

                Keep your final reply short and factual:
                - what you actually changed or ran
                - what you submitted/launched
                - what should happen next if the task is still incomplete

                Constraint:
                {session.constraint}

                Success criteria:
                {session.success_criteria}

                Current observation:
                {observation.summary}

                Your task:
                {task.title}

                Instructions:
                {task.instructions}

                Return a short structured summary of what you did.
                """
            ).strip()
            manifest = self.runtime.begin_run(
                self._context_pack(project=session.project, goal=task.title, mode=CollaborationMode.DISCUSSION),
                assignments=[ProviderAssignment(role=AgentRole.WRITER, provider=actor.provider)],
            )
            try:
                artifact = self.runtime.run_role(
                    manifest,
                    role=AgentRole.WRITER,
                    provider=actor.provider,
                    request=AgentRequest(
                        role=AgentRole.WRITER,
                        prompt=prompt,
                        cwd=str(self._project_code_dir(session.project)),
                        allowed_tools=["Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "LS"],
                    ),
                )
            except (AgentAdapterError, Exception) as exc:
                self.runtime.finish_run(manifest)
                results.append(WorkerResult(
                    worker=actor.name,
                    status="error",
                    summary=f"Worker {actor.name} failed: {exc}",
                    actions_taken=[],
                    outputs=[],
                    follow_up="Retry this task or reassign to another worker.",
                ))
                continue
            after_status = self._git_status_lines(code_dir)
            changed_paths = self._changed_paths(before_status, after_status)
            diff_excerpt = self._git_diff_excerpt(code_dir, changed_paths)
            results.append(
                self._summarize_worker_result(
                    session=session,
                    actor=actor,
                    task=task,
                    observation=observation,
                    raw_output=artifact.raw_output,
                    changed_paths=changed_paths,
                    diff_excerpt=diff_excerpt,
                )
            )
            self.runtime.finish_run(manifest)
        return results

    def _deliberate(self, *, session: AutoSessionRecord, observation: AutoObservation, actors: list[AutoActor]) -> list[DiscussionNote]:
        context = self._context_pack(project=session.project, goal="Discuss new experiment results", mode=CollaborationMode.DISCUSSION)
        assignments = [ProviderAssignment(role=AgentRole.DISCUSSANT, provider=actor.provider) for actor in actors]
        manifest = self.runtime.begin_run(context, assignments=assignments)
        notes: list[DiscussionNote] = []

        for actor in actors:
            prior_notes = "\n".join(
                f"- {note.actor}: {note.summary} | next: {note.next_step}"
                for note in notes
            ) or "(no prior peer comments yet)"
            prompt = textwrap.dedent(
                f"""
                You are one of three peers reviewing new experiment evidence.
                Do not assume authority over the others.

                Constraint:
                {session.constraint}

                Success criteria:
                {session.success_criteria}

                Results snapshot:
                {observation.summary}

                Prior peer comments:
                {prior_notes}

                Give your current judgment, the strongest evidence, and the next best step.
                """
            ).strip()
            try:
                artifact = self.runtime.run_role(
                    manifest,
                    role=AgentRole.DISCUSSANT,
                    provider=actor.provider,
                    request=AgentRequest(
                        role=AgentRole.DISCUSSANT,
                        prompt=prompt,
                        output_schema=self._discussion_schema(),
                        cwd=str(self._project_code_dir(session.project)),
                    ),
                )
                payload = artifact.output if isinstance(artifact.output, dict) else {}
                notes.append(
                    DiscussionNote(
                        actor=actor.name,
                        summary=str(payload.get("summary", "")).strip(),
                        evidence=[str(item) for item in (payload.get("evidence", []) or [])],
                        next_step=str(payload.get("next_step", "")).strip(),
                    )
                )
            except (AgentAdapterError, Exception):
                notes.append(
                    DiscussionNote(
                        actor=actor.name,
                        summary=f"{actor.name} timed out or errored during deliberation.",
                        evidence=[],
                        next_step="Retry in next iteration.",
                    )
                )
        self.runtime.finish_run(manifest)
        return notes

    def _git_status_lines(self, code_dir: Path) -> list[str]:
        try:
            output = subprocess.check_output(
                ["git", "-C", str(code_dir), "status", "--short"],
                text=True,
            )
        except Exception:
            return []
        return [line.rstrip() for line in output.splitlines() if line.strip()]

    def _changed_paths(self, before: list[str], after: list[str]) -> list[str]:
        before_set = set(before)
        paths: list[str] = []
        for line in after:
            if line in before_set:
                continue
            if len(line) >= 4:
                paths.append(line[3:].strip())
        return paths[:8]

    def _git_diff_excerpt(self, code_dir: Path, changed_paths: list[str]) -> str:
        if not changed_paths:
            return ""
        try:
            output = subprocess.check_output(
                ["git", "-C", str(code_dir), "diff", "--", *changed_paths[:4]],
                text=True,
            ).strip()
        except Exception:
            return ""
        if not output:
            return ""
        return "\n".join(output.splitlines()[:120])

    def _summarize_worker_result(
        self,
        *,
        session: AutoSessionRecord,
        actor: AutoActor,
        task: WorkerTask,
        observation: AutoObservation,
        raw_output: str,
        changed_paths: list[str],
        diff_excerpt: str,
    ) -> WorkerResult:
        prompt = textwrap.dedent(
            f"""
            Summarize the completed worker task into a compact structured result.
            Prefer concrete execution evidence over intentions.

            Worker: {actor.name}
            Task title: {task.title}
            Task instructions:
            {task.instructions}

            Observation before execution:
            {observation.summary}

            Worker raw reply:
            {raw_output.strip() or "(empty)"}

            Changed paths:
            {chr(10).join(f"- {path}" for path in changed_paths) or "(none)"}

            Git diff excerpt:
            {diff_excerpt or "(no diff excerpt)"}
            """
        ).strip()
        manifest = self.runtime.begin_run(
            self._context_pack(
                project=session.project,
                goal=f"Summarize worker result for {task.title}",
                mode=CollaborationMode.DISCUSSION,
            ),
            assignments=[ProviderAssignment(role=AgentRole.SYNTHESIZER, provider=actor.provider)],
        )
        try:
            artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.SYNTHESIZER,
                provider=actor.provider,
                request=AgentRequest(
                    role=AgentRole.SYNTHESIZER,
                    prompt=prompt,
                    output_schema=self._worker_schema(),
                    cwd=str(self._project_code_dir(session.project)),
                ),
            )
            payload = artifact.output if isinstance(artifact.output, dict) else {}
        except Exception:
            payload = {}
        finally:
            self.runtime.finish_run(manifest)

        actions_taken = [str(item) for item in (payload.get("actions_taken", []) or [])]
        if changed_paths and "modified project files" not in actions_taken:
            actions_taken.append("modified project files")
        outputs = [str(item) for item in (payload.get("outputs", []) or [])]
        outputs.extend(path for path in changed_paths if path not in outputs)
        summary = str(payload.get("summary", "")).strip()
        if not summary:
            summary = raw_output.strip().splitlines()[0][:240] if raw_output.strip() else "Task executed."
        status = str(payload.get("status", "completed")).strip() or "completed"
        follow_up = str(payload.get("follow_up", "")).strip()
        return WorkerResult(
            worker=actor.name,
            status=status,
            summary=summary,
            actions_taken=actions_taken,
            outputs=outputs,
            follow_up=follow_up,
        )

    def _observation_unchanged(self, session: AutoSessionRecord, observation: AutoObservation) -> bool:
        """Return True if observation is materially the same as last time."""
        if not session.last_observation_summary:
            return False
        # Normalize whitespace for comparison
        prev = session.last_observation_summary.strip()
        curr = observation.summary.strip()
        return prev == curr

    def _trigger_from_observation(self, observation: AutoObservation) -> str:
        if observation.has_results:
            return "results_available"
        if observation.has_failures:
            return "failure_detected"
        if observation.has_running:
            return "progress_check"
        return "idle_check"

    def _supervisor_schema(self) -> dict:
        task_props = {
            "worker": {"type": "string"},
            "title": {"type": "string"},
            "instructions": {"type": "string"},
        }
        props = {
            "action": {"type": "string", "enum": [item.value for item in AutoAction]},
            "summary": {"type": "string"},
            "worker_tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": task_props,
                    "required": list(task_props.keys()),
                },
            },
            "human_needed": {"type": "boolean"},
            "success_reached": {"type": "boolean"},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
            "required": list(props.keys()),
        }

    def _worker_schema(self) -> dict:
        props = {
            "status": {"type": "string"},
            "summary": {"type": "string"},
            "actions_taken": {"type": "array", "items": {"type": "string"}},
            "outputs": {"type": "array", "items": {"type": "string"}},
            "follow_up": {"type": "string"},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
            "required": list(props.keys()),
        }

    def _discussion_schema(self) -> dict:
        props = {
            "summary": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "next_step": {"type": "string"},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
            "required": list(props.keys()),
        }
