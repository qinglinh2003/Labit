from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml

from labit.agents.models import AgentRequest, AgentRole, ProviderKind
from labit.agents.orchestrator import ProviderRegistry
from labit.agents.providers import resolve_provider_kind
from labit.capture.service import CaptureService
from labit.context.events import SessionEventKind
from labit.context.store import SessionContextStore
from labit.experiments.service import ExperimentService
from labit.hypotheses.service import HypothesisService
from labit.investigations.service import InvestigationService
from labit.memory.store import MemoryStore
from labit.papers.service import PaperService
from labit.paths import RepoPaths
from labit.reports.models import (
    DailyActivityItem,
    DailyCommitItem,
    DailyEventItem,
    DailySummaryDraft,
    DailySummaryInputs,
    DailySummaryResult,
)
from labit.services.project_service import ProjectService


class DailySummaryService:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        project_service: ProjectService | None = None,
        hypothesis_service: HypothesisService | None = None,
        experiment_service: ExperimentService | None = None,
        capture_service: CaptureService | None = None,
        investigation_service: InvestigationService | None = None,
        paper_service: PaperService | None = None,
        memory_store: MemoryStore | None = None,
        session_context_store: SessionContextStore | None = None,
        registry: ProviderRegistry | None = None,
    ):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)
        self.hypothesis_service = hypothesis_service or HypothesisService(paths)
        self.experiment_service = experiment_service or ExperimentService(paths)
        self.capture_service = capture_service or CaptureService(paths)
        self.investigation_service = investigation_service or InvestigationService(paths)
        self.paper_service = paper_service or PaperService(paths)
        self.memory_store = memory_store or MemoryStore(paths)
        self.session_context_store = session_context_store or SessionContextStore(paths)
        self.registry = registry or ProviderRegistry.default()

    def generate(
        self,
        *,
        project: str,
        target_date: date | None = None,
        provider: str | ProviderKind | None = None,
    ) -> DailySummaryResult:
        resolved = self._require_project(project)
        target = target_date or self._local_now().date()
        timezone_text = self._timezone_label()
        inputs = self.collect_inputs(project=resolved, target_date=target, timezone_text=timezone_text)
        markdown = self._draft_markdown(inputs=inputs, provider=provider)
        markdown_path, yaml_path = self._write_outputs(project=resolved, target_date=target, markdown=markdown, inputs=inputs)
        return DailySummaryResult(
            project=resolved,
            date=target.isoformat(),
            timezone=timezone_text,
            markdown_path=str(markdown_path.relative_to(self.paths.root)),
            yaml_path=str(yaml_path.relative_to(self.paths.root)),
            markdown=markdown,
        )

    def collect_inputs(self, *, project: str, target_date: date, timezone_text: str) -> DailySummaryInputs:
        resolved = self._require_project(project)
        events = self._collect_events(project=resolved, target_date=target_date)
        event_counts = dict(sorted(Counter(item.kind for item in events).items()))
        discussion_syntheses = [
            DailyActivityItem(
                title=item.summary,
                summary=item.summary,
                created_at=item.created_at,
                refs=item.evidence_refs,
            )
            for item in events
            if item.kind == SessionEventKind.DISCUSSION_SYNTHESIS.value
        ]
        hypotheses_created, hypotheses_updated, hypotheses_closed = self._collect_hypotheses(project=resolved, target_date=target_date)
        experiments_created, experiments_updated, tasks_submitted, tasks_finished = self._collect_experiments(project=resolved, target_date=target_date)
        reports = self._collect_reports(project=resolved, target_date=target_date)
        ideas = self._collect_capture_items(project=resolved, kind="idea", target_date=target_date)
        notes = self._collect_capture_items(project=resolved, kind="note", target_date=target_date)
        todos = self._collect_capture_items(project=resolved, kind="todo", target_date=target_date)
        papers_pulled, papers_ingested = self._collect_papers(project=resolved, target_date=target_date)
        memory_updates = self._collect_memory(project=resolved, target_date=target_date)
        research_os_commits = self._collect_git_commits(self.paths.root, repo_label="research-os", target_date=target_date)
        project_code_dir = self.paths.vault_projects_dir / resolved / "code"
        project_code_commits = self._collect_git_commits(project_code_dir, repo_label="project-code", target_date=target_date)
        return DailySummaryInputs(
            project=resolved,
            date=target_date.isoformat(),
            timezone=timezone_text,
            event_counts=event_counts,
            events=events,
            discussion_syntheses=discussion_syntheses,
            hypotheses_created=hypotheses_created,
            hypotheses_updated=hypotheses_updated,
            hypotheses_closed=hypotheses_closed,
            experiments_created=experiments_created,
            experiments_updated=experiments_updated,
            tasks_submitted=tasks_submitted,
            tasks_finished=tasks_finished,
            reports=reports,
            ideas=ideas,
            notes=notes,
            todos=todos,
            papers_pulled=papers_pulled,
            papers_ingested=papers_ingested,
            memory_updates=memory_updates,
            research_os_commits=research_os_commits,
            project_code_commits=project_code_commits,
        )

    def _draft_markdown(self, *, inputs: DailySummaryInputs, provider: str | ProviderKind | None = None) -> str:
        provider_kind = resolve_provider_kind(provider)
        request = AgentRequest(
            role=AgentRole.WRITER,
            prompt=self._build_prompt(inputs),
            cwd=str(self.paths.root),
            output_schema=self._draft_schema(),
            timeout_seconds=120,
            extra_args=self._extra_args(provider_kind),
        )
        try:
            response = self.registry.get(provider_kind).run(request)
            payload = response.structured_output
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                raise ValueError("Daily summary drafter returned an invalid payload.")
            draft = DailySummaryDraft.model_validate(payload)
            return self._render_markdown(inputs=inputs, draft=draft)
        except Exception:
            return self._render_markdown(inputs=inputs, draft=self._fallback_draft(inputs))

    def _write_outputs(self, *, project: str, target_date: date, markdown: str, inputs: DailySummaryInputs) -> tuple[Path, Path]:
        daily_dir = self.paths.vault_projects_dir / project / "docs" / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
        stem = target_date.isoformat()
        markdown_path = daily_dir / f"{stem}.md"
        yaml_path = daily_dir / f"{stem}.yaml"
        self._atomic_write(markdown_path, markdown.rstrip() + "\n")
        self._atomic_write(yaml_path, yaml.safe_dump(inputs.model_dump(mode="json"), sort_keys=False, allow_unicode=False))
        return markdown_path, yaml_path

    def _build_prompt(self, inputs: DailySummaryInputs) -> str:
        snapshot = json.dumps(inputs.model_dump(mode="json"), indent=2, sort_keys=True)
        return f"""You are writing a LABIT daily summary for one research project.

Return JSON only. Do not add markdown fences or commentary.

Write from the provided day-specific structured inputs. Be faithful to what actually happened on that date.
Do not invent experiments, commits, hypotheses, or results that are not present in the inputs.

The final markdown will contain these sections:
- What Moved Today
- Evidence Produced
- Hypothesis State
- Papers, Reports, And Captures
- Code Changes
- Open Loops
- Tomorrow Plan
- Free Write

Requirements:
- Each list field should contain 0 to 5 concise bullet items.
- `free_write` should be 1 to 3 short paragraphs and may connect the dots across sections.
- Prefer concrete artifact names and ids when available.
- Focus on what matters for tomorrow's work, not exhaustive bookkeeping.

Inputs:
{snapshot}
"""

    def _draft_schema(self) -> dict:
        properties = {
            "what_moved_today": {"type": "array", "items": {"type": "string"}},
            "evidence_produced": {"type": "array", "items": {"type": "string"}},
            "hypothesis_state": {"type": "array", "items": {"type": "string"}},
            "papers_reports_and_captures": {"type": "array", "items": {"type": "string"}},
            "code_changes": {"type": "array", "items": {"type": "string"}},
            "open_loops": {"type": "array", "items": {"type": "string"}},
            "tomorrow_plan": {"type": "array", "items": {"type": "string"}},
            "free_write": {"type": "string"},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list(properties.keys()),
        }

    def _render_markdown(self, *, inputs: DailySummaryInputs, draft: DailySummaryDraft) -> str:
        sections = [
            ("What Moved Today", draft.what_moved_today),
            ("Evidence Produced", draft.evidence_produced),
            ("Hypothesis State", draft.hypothesis_state),
            ("Papers, Reports, And Captures", draft.papers_reports_and_captures),
            ("Code Changes", draft.code_changes),
            ("Open Loops", draft.open_loops),
            ("Tomorrow Plan", draft.tomorrow_plan),
        ]
        lines = [
            f"# Daily Summary — {inputs.date}",
            f"**Project**: {inputs.project}",
            f"**Timezone**: {inputs.timezone}",
            "",
        ]
        for title, items in sections:
            lines.append(f"## {title}")
            lines.append("")
            if items:
                lines.extend(f"- {item}" for item in items)
            else:
                lines.append("- None.")
            lines.append("")
        lines.extend(["## Free Write", "", draft.free_write or "No major narrative summary was generated.", ""])
        return "\n".join(lines).rstrip()

    def _fallback_draft(self, inputs: DailySummaryInputs) -> DailySummaryDraft:
        what_moved = []
        what_moved.extend(item.title for item in inputs.hypotheses_created[:2])
        what_moved.extend(item.title for item in inputs.experiments_created[:2])
        evidence = [item.title or item.summary for item in inputs.tasks_finished[:4]]
        hypothesis_state = [item.title or item.summary for item in [*inputs.hypotheses_created, *inputs.hypotheses_closed][:5]]
        papers_reports = [
            item.title
            for item in [*inputs.papers_ingested, *inputs.papers_pulled, *inputs.reports, *inputs.ideas][:5]
        ]
        code_changes = [f"{item.repo_label}: {item.sha[:7]} {item.message}" for item in [*inputs.research_os_commits, *inputs.project_code_commits][:5]]
        open_loops = [item.title or item.summary for item in [*inputs.todos, *inputs.discussion_syntheses][:5]]
        tomorrow_plan = open_loops[:3] or ["Continue the highest-priority open loop from today."]
        free_write = (
            "Today’s activity was summarized from structured LABIT artifacts. "
            "Use the sections above as the source of truth for what changed, what evidence was produced, and what still needs attention tomorrow."
        )
        return DailySummaryDraft(
            what_moved_today=what_moved[:5],
            evidence_produced=evidence[:5],
            hypothesis_state=hypothesis_state[:5],
            papers_reports_and_captures=papers_reports[:5],
            code_changes=code_changes[:5],
            open_loops=open_loops[:5],
            tomorrow_plan=tomorrow_plan[:5],
            free_write=free_write,
        )

    def _collect_events(self, *, project: str, target_date: date) -> list[DailyEventItem]:
        items: list[DailyEventItem] = []
        if not self.paths.conversations_dir.exists():
            return items
        for events_path in sorted(self.paths.conversations_dir.glob("*/events.jsonl")):
            session_id = events_path.parent.name
            for event in self.session_context_store.load_events(session_id):
                if event.project != project:
                    continue
                if not self._matches_date(event.created_at, target_date):
                    continue
                items.append(
                    DailyEventItem(
                        kind=event.kind.value,
                        summary=event.summary,
                        created_at=event.created_at,
                        actor=event.actor,
                        evidence_refs=event.evidence_refs,
                    )
                )
        return sorted(items, key=lambda item: item.created_at)

    def _collect_hypotheses(self, *, project: str, target_date: date) -> tuple[list[DailyActivityItem], list[DailyActivityItem], list[DailyActivityItem]]:
        created: list[DailyActivityItem] = []
        updated: list[DailyActivityItem] = []
        closed: list[DailyActivityItem] = []
        for summary in self.hypothesis_service.list_hypotheses(project):
            detail = self.hypothesis_service.load_hypothesis(project, summary.hypothesis_id)
            record = detail.record
            if self._matches_date(record.created_at, target_date):
                created.append(
                    DailyActivityItem(
                        title=f"{record.hypothesis_id} · {record.title}",
                        summary=record.claim,
                        path=detail.path,
                        status=f"{record.state.value}/{record.resolution.value}",
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                        refs=[f"hypothesis:{record.hypothesis_id}", *[f"paper:{item}" for item in record.source_paper_ids]],
                    )
                )
            elif self._matches_date(record.updated_at, target_date):
                updated.append(
                    DailyActivityItem(
                        title=f"{record.hypothesis_id} · {record.title}",
                        summary=record.result_summary or record.claim,
                        path=detail.path,
                        status=f"{record.state.value}/{record.resolution.value}",
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                        refs=[f"hypothesis:{record.hypothesis_id}"],
                    )
                )
            if record.closed_at and self._matches_date(record.closed_at, target_date):
                closed.append(
                    DailyActivityItem(
                        title=f"{record.hypothesis_id} · {record.title}",
                        summary=record.result_summary or record.decision_rationale or record.claim,
                        path=detail.path,
                        status=f"{record.state.value}/{record.resolution.value}",
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                        refs=[f"hypothesis:{record.hypothesis_id}"],
                    )
                )
        return created, updated, closed

    def _collect_experiments(self, *, project: str, target_date: date) -> tuple[list[DailyActivityItem], list[DailyActivityItem], list[DailyActivityItem], list[DailyActivityItem]]:
        created: list[DailyActivityItem] = []
        updated: list[DailyActivityItem] = []
        submitted: list[DailyActivityItem] = []
        finished: list[DailyActivityItem] = []
        for summary in self.experiment_service.list_experiments(project):
            detail = self.experiment_service.load_experiment(project, summary.experiment_id)
            record = detail.record
            if self._matches_date(record.created_at, target_date):
                created.append(
                    DailyActivityItem(
                        title=f"{record.experiment_id} · {record.title}",
                        summary=record.objective,
                        path=detail.path,
                        status=f"{record.status.value}/{record.assessment.value}",
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                        refs=[f"experiment:{record.experiment_id}", f"hypothesis:{record.parent_id}"],
                    )
                )
            elif self._matches_date(record.updated_at, target_date):
                updated.append(
                    DailyActivityItem(
                        title=f"{record.experiment_id} · {record.title}",
                        summary=record.result_summary or record.objective,
                        path=detail.path,
                        status=f"{record.status.value}/{record.assessment.value}",
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                        refs=[f"experiment:{record.experiment_id}"],
                    )
                )
            for task in detail.tasks:
                task_record = self.experiment_service.load_task(project, record.experiment_id, task.task_id)
                if task_record.runtime.submitted_at and self._matches_date(task_record.runtime.submitted_at, target_date):
                    submitted.append(
                        DailyActivityItem(
                            title=f"{record.experiment_id}/{task_record.task_id} · {task_record.title}",
                            summary=task_record.spec.command or task_record.spec.entrypoint,
                            path=task.path,
                            status=task_record.status.value,
                            created_at=task_record.created_at,
                            updated_at=task_record.updated_at,
                            refs=[f"experiment:{record.experiment_id}", f"task:{task_record.task_id}"],
                        )
                    )
                if task_record.runtime.finished_at and self._matches_date(task_record.runtime.finished_at, target_date):
                    finished.append(
                        DailyActivityItem(
                            title=f"{record.experiment_id}/{task_record.task_id} · {task_record.title}",
                            summary=task_record.results.summary or task_record.results.error or task_record.spec.command,
                            path=task.path,
                            status=task_record.status.value,
                            created_at=task_record.created_at,
                            updated_at=task_record.updated_at,
                            refs=[f"experiment:{record.experiment_id}", f"task:{task_record.task_id}", *task_record.results.artifact_refs[:4]],
                        )
                    )
        return created, updated, submitted, finished

    def _collect_reports(self, *, project: str, target_date: date) -> list[DailyActivityItem]:
        items: list[DailyActivityItem] = []
        for report in self.investigation_service.list_reports(project):
            if not self._matches_date(report.date, target_date):
                continue
            items.append(
                DailyActivityItem(
                    title=report.title,
                    summary=report.summary,
                    path=report.path,
                    status=report.status,
                    created_at=report.date,
                    refs=[f"report:{report.path}"],
                )
            )
        return items

    def _collect_capture_items(self, *, project: str, kind: str, target_date: date) -> list[DailyActivityItem]:
        if kind == "idea":
            records = self.capture_service.list_ideas(project)
        elif kind == "note":
            records = self.capture_service.list_notes(project)
        else:
            records = self.capture_service.list_todos(project)
        items: list[DailyActivityItem] = []
        for record in records:
            if not self._matches_date(record.created_at, target_date):
                continue
            items.append(
                DailyActivityItem(
                    title=record.title,
                    path=record.path,
                    created_at=record.created_at,
                    summary=record.source,
                    refs=[f"{kind}:{record.path}"],
                )
            )
        return items

    def _collect_papers(self, *, project: str, target_date: date) -> tuple[list[DailyActivityItem], list[DailyActivityItem]]:
        pulled: list[DailyActivityItem] = []
        ingested: list[DailyActivityItem] = []
        for entry in self.paper_service.list_project_index_entries(project):
            try:
                record = self.paper_service.load_project_record(project, entry.paper_id)
            except Exception:
                continue

            touched_today = self._matches_date(record.added_at, target_date) or self._matches_date(record.updated_at, target_date)
            if not touched_today:
                continue

            item = DailyActivityItem(
                title=record.title,
                summary=f"{record.paper_id} · {record.status.value}",
                path=entry.path,
                status=record.status.value,
                created_at=record.added_at,
                updated_at=record.updated_at,
                refs=[f"paper:{record.paper_id}"],
            )
            if record.status.value == "ingested":
                ingested.append(item)
            else:
                pulled.append(item)
        return pulled, ingested

    def _collect_memory(self, *, project: str, target_date: date) -> list[DailyActivityItem]:
        items: list[DailyActivityItem] = []
        for record in self.memory_store.list_records(project, include_inactive=True):
            if not self._matches_date(record.updated_at, target_date):
                continue
            items.append(
                DailyActivityItem(
                    title=record.title,
                    summary=record.summary,
                    path=str(self.memory_store.entry_path(project, record.memory_id).relative_to(self.paths.root)),
                    status=record.status.value,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    refs=record.evidence_refs[:6],
                )
            )
        return items

    def _collect_git_commits(self, repo_path: Path, *, repo_label: str, target_date: date) -> list[DailyCommitItem]:
        if not (repo_path / ".git").exists():
            return []
        start_dt = datetime.combine(target_date, time.min, tzinfo=self._local_now().tzinfo)
        end_dt = start_dt + timedelta(days=1)
        command = [
            "git",
            "log",
            f"--since={start_dt.isoformat()}",
            f"--until={end_dt.isoformat()}",
            "--date=iso-strict",
            "--pretty=format:%H%x1f%ad%x1f%s",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=repo_path,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        commits: list[DailyCommitItem] = []
        for line in (result.stdout or "").splitlines():
            if not line.strip():
                continue
            parts = line.split("\x1f")
            if len(parts) != 3:
                continue
            sha, authored_at, message = parts
            commits.append(
                DailyCommitItem(
                    sha=sha,
                    message=message,
                    authored_at=authored_at,
                    repo_label=repo_label,
                    repo_path=str(repo_path.relative_to(self.paths.root)),
                )
            )
        return commits

    def _render_list(self, items: list[DailyActivityItem], *, fallback: str = "(none)", limit: int = 5) -> list[str]:
        if not items:
            return [fallback]
        rendered: list[str] = []
        for item in items[:limit]:
            line = item.title
            if item.status:
                line += f" [{item.status}]"
            if item.summary:
                line += f" :: {item.summary}"
            rendered.append(line)
        return rendered

    def _matches_date(self, value: str | None, target_date: date) -> bool:
        if not value:
            return False
        text = str(value).strip()
        if not text:
            return False
        if len(text) == 10 and text.count("-") == 2:
            return text == target_date.isoformat()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return False
        if dt.tzinfo is None:
            return dt.date() == target_date
        return dt.astimezone(self._local_now().tzinfo).date() == target_date

    def _require_project(self, project: str) -> str:
        resolved = self.project_service.resolve_project_name(project)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{project}' not found. Available projects: {', '.join(self.project_service.list_project_names()) or '(none)'}"
            )
        return resolved

    def _local_now(self) -> datetime:
        return datetime.now().astimezone()

    def _timezone_label(self) -> str:
        now = self._local_now()
        return now.tzname() or now.isoformat()

    def _extra_args(self, provider: ProviderKind) -> list[str]:
        if provider == ProviderKind.CLAUDE:
            return ["--effort", "medium"]
        if provider == ProviderKind.CODEX:
            return ["-c", 'model_reasoning_effort="medium"']
        return []

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
