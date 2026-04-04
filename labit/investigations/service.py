from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from labit.agents.context import ContextBuilder
from labit.agents.models import (
    AgentRequest,
    AgentRole,
    CollaborationMode,
    ProviderAssignment,
    ProviderKind,
    SynthesisArtifact,
    TaskSpec,
)
from labit.agents.orchestrator import AgentRuntime
from labit.agents.providers import discussion_provider_kinds, resolve_provider_kind
from labit.chat.models import ChatMode
from labit.investigations.models import InvestigationReportSummary, InvestigationResult
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


class InvestigationService:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        project_service: ProjectService | None = None,
        runtime: AgentRuntime | None = None,
        context_builder: ContextBuilder | None = None,
    ):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)
        self.runtime = runtime or AgentRuntime(paths)
        self.context_builder = context_builder or ContextBuilder(paths)

    def list_reports(self, project: str) -> list[InvestigationReportSummary]:
        reports_dir = self._reports_dir(project)
        if not reports_dir.exists():
            return []
        reports: list[InvestigationReportSummary] = []
        for path in sorted(reports_dir.glob("*.md"), reverse=True):
            reports.append(self._parse_report(path))
        return reports

    def find_related_reports(self, project: str, topic: str, *, limit: int = 5) -> list[InvestigationReportSummary]:
        topic_tokens = self._tokenize(topic)
        if not topic_tokens:
            return []
        ranked: list[InvestigationReportSummary] = []
        for report in self.list_reports(project):
            haystack = " ".join([report.title, report.topic, report.summary])
            score = self._overlap_score(topic_tokens, self._tokenize(haystack))
            if score <= 0:
                continue
            ranked.append(report.model_copy(update={"score": score}))
        ranked.sort(key=lambda item: (-item.score, item.path))
        return ranked[:limit]

    def investigate(
        self,
        *,
        project: str,
        topic: str,
        mode: ChatMode,
        provider: str | None = None,
        second_provider: str | None = None,
        source_session_id: str = "",
        session_title: str = "",
        transcript_excerpt: str = "",
        session_context: str = "",
    ) -> InvestigationResult:
        task = TaskSpec(
            kind="investigate",
            goal=f"Investigate for project {project}: {topic}",
            mode=CollaborationMode.DISCUSSION,
            expected_outputs=["investigation_report"],
            metadata={"project": project, "topic": topic},
        )
        context = self.context_builder.build(task, project_name=project)
        related_reports = self.find_related_reports(project, topic)
        primary_provider, secondary_provider = self._resolve_providers(mode, provider=provider, second_provider=second_provider)
        assignments = [ProviderAssignment(role=AgentRole.WRITER, provider=primary_provider)]
        if mode != ChatMode.SINGLE:
            assignments.append(ProviderAssignment(role=AgentRole.REVIEWER, provider=secondary_provider))
        if mode == ChatMode.PARALLEL:
            assignments.append(ProviderAssignment(role=AgentRole.SYNTHESIZER, provider=primary_provider))

        manifest = self.runtime.begin_run(context, assignments=assignments)
        final_report = ""

        if mode == ChatMode.SINGLE:
            writer_artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.WRITER,
                provider=primary_provider,
                request=AgentRequest(
                    role=AgentRole.WRITER,
                    prompt=self._writer_prompt(
                        project=project,
                        topic=topic,
                        related_reports=related_reports,
                        source_session_id=source_session_id,
                        session_title=session_title,
                        transcript_excerpt=transcript_excerpt,
                        session_context=session_context,
                    ),
                    cwd=str(self.paths.root),
                    timeout_seconds=180,
                    allowed_tools=self._investigate_allowed_tools(primary_provider),
                    extra_args=self._investigate_agent_extra_args(primary_provider),
                ),
            )
            final_report = writer_artifact.raw_output.strip()
        elif mode == ChatMode.ROUND_ROBIN:
            writer_artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.WRITER,
                provider=primary_provider,
                request=AgentRequest(
                    role=AgentRole.WRITER,
                    prompt=self._writer_prompt(
                        project=project,
                        topic=topic,
                        related_reports=related_reports,
                        source_session_id=source_session_id,
                        session_title=session_title,
                        transcript_excerpt=transcript_excerpt,
                        session_context=session_context,
                    ),
                    cwd=str(self.paths.root),
                    timeout_seconds=180,
                    allowed_tools=self._investigate_allowed_tools(primary_provider),
                    extra_args=self._investigate_agent_extra_args(primary_provider),
                ),
            )
            reviewer_artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.REVIEWER,
                provider=secondary_provider,
                request=AgentRequest(
                    role=AgentRole.REVIEWER,
                    prompt=self._round_robin_revision_prompt(
                        project=project,
                        topic=topic,
                        related_reports=related_reports,
                        prior_report=writer_artifact.raw_output,
                        prior_provider=primary_provider,
                        source_session_id=source_session_id,
                        session_title=session_title,
                        transcript_excerpt=transcript_excerpt,
                        session_context=session_context,
                    ),
                    cwd=str(self.paths.root),
                    timeout_seconds=180,
                    allowed_tools=self._investigate_allowed_tools(secondary_provider),
                    extra_args=self._investigate_agent_extra_args(secondary_provider),
                ),
            )
            final_report = reviewer_artifact.raw_output.strip()
        else:
            writer_artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.WRITER,
                provider=primary_provider,
                request=AgentRequest(
                    role=AgentRole.WRITER,
                    prompt=self._writer_prompt(
                        project=project,
                        topic=topic,
                        related_reports=related_reports,
                        source_session_id=source_session_id,
                        session_title=session_title,
                        transcript_excerpt=transcript_excerpt,
                        session_context=session_context,
                    ),
                    cwd=str(self.paths.root),
                    timeout_seconds=180,
                    allowed_tools=self._investigate_allowed_tools(primary_provider),
                    extra_args=self._investigate_agent_extra_args(primary_provider),
                ),
            )
            reviewer_artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.REVIEWER,
                provider=secondary_provider,
                request=AgentRequest(
                    role=AgentRole.REVIEWER,
                    prompt=self._writer_prompt(
                        project=project,
                        topic=topic,
                        related_reports=related_reports,
                        source_session_id=source_session_id,
                        session_title=session_title,
                        transcript_excerpt=transcript_excerpt,
                        session_context=session_context,
                        perspective=(
                            f"Investigate independently as {secondary_provider.value}. "
                            "Do not look at any prior draft from the other agent."
                        ),
                    ),
                    cwd=str(self.paths.root),
                    timeout_seconds=180,
                    allowed_tools=self._investigate_allowed_tools(secondary_provider),
                    extra_args=self._investigate_agent_extra_args(secondary_provider),
                ),
            )
            synthesizer_artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.SYNTHESIZER,
                provider=primary_provider,
                request=AgentRequest(
                    role=AgentRole.SYNTHESIZER,
                    prompt=self._parallel_synthesis_prompt(
                        project=project,
                        topic=topic,
                        related_reports=related_reports,
                        draft_a=writer_artifact.raw_output,
                        draft_b=reviewer_artifact.raw_output,
                        provider_a=primary_provider,
                        provider_b=secondary_provider,
                        source_session_id=source_session_id,
                        session_title=session_title,
                        transcript_excerpt=transcript_excerpt,
                        session_context=session_context,
                    ),
                    cwd=str(self.paths.root),
                    timeout_seconds=180,
                    allowed_tools=self._investigate_allowed_tools(primary_provider),
                    extra_args=self._investigate_agent_extra_args(primary_provider),
                ),
            )
            final_report = synthesizer_artifact.raw_output.strip()

        if not final_report:
            raise RuntimeError("Investigation produced an empty report.")

        title = self._extract_title(final_report) or self._fallback_title(topic)
        summary = self._extract_summary(final_report)
        report_path = self._write_report(project=project, title=title, markdown=final_report)
        self.runtime.record_synthesis(
            manifest,
            SynthesisArtifact(
                run_id=manifest.run_id,
                summary=summary or f"Investigated topic: {topic}",
                claims=[title],
                evidence=[item.path for item in related_reports[:3]],
                open_questions=[],
                recommended_next_step="Review the report and decide whether follow-up experiments are needed.",
            ),
        )
        self.runtime.finish_run(manifest)
        return InvestigationResult(
            project=project,
            topic=topic,
            mode=mode,
            run_id=manifest.run_id,
            report_path=str(report_path.relative_to(self.paths.root)),
            title=title,
            summary=summary,
            related_reports=related_reports,
        )

    def _resolve_providers(
        self,
        mode: ChatMode,
        *,
        provider: str | None,
        second_provider: str | None,
    ) -> tuple[ProviderKind, ProviderKind]:
        if mode == ChatMode.SINGLE:
            kind = resolve_provider_kind(provider)
            return kind, kind
        if provider in (None, "auto") and second_provider in (None, "auto"):
            return discussion_provider_kinds()
        first = resolve_provider_kind(provider)
        second = resolve_provider_kind(second_provider or "auto")
        return first, second

    def _writer_prompt(
        self,
        *,
        project: str,
        topic: str,
        related_reports: list[InvestigationReportSummary],
        source_session_id: str = "",
        session_title: str = "",
        transcript_excerpt: str = "",
        session_context: str = "",
        perspective: str = "",
    ) -> str:
        report_context = self._related_reports_block(related_reports)
        extra = f"\nPerspective:\n{perspective}\n" if perspective.strip() else ""
        session_block = self._session_block(
            source_session_id=source_session_id,
            session_title=session_title,
            transcript_excerpt=transcript_excerpt,
            session_context=session_context,
        )
        return f"""You are writing an investigation report for LABIT.

Project: {project}
Topic: {topic}
{extra}
First, investigate the topic thoroughly inside the repository. Read code, configs, docs, and outputs as needed. Follow leads if they materially affect the conclusion, but stay focused on the topic.

Related prior reports:
{report_context}

Current LABIT session context:
{session_block}

Write a complete markdown report in exactly this format:

# {{Title}}
**Date**: {datetime.now(UTC).date().isoformat()}
**Project**: {project}
**Topic**: {topic}
**Status**: complete | partial | needs-followup

## Summary
2-3 sentence summary of the key findings.

## Context
Why this was investigated.

## Findings
Detailed findings by sub-topic. Include file paths and line numbers for code findings when relevant.

## Evidence
Concrete evidence: command outputs, metrics, snippets, or observed behavior.

## Open questions
Anything unresolved.

## Related
Links to related reports, hypotheses, or other connected work.

Be specific and evidence-driven. Do not invent files, metrics, or behavior. If something was not verified, say so clearly.
"""

    def _round_robin_revision_prompt(
        self,
        *,
        project: str,
        topic: str,
        related_reports: list[InvestigationReportSummary],
        prior_report: str,
        prior_provider: ProviderKind,
        source_session_id: str = "",
        session_title: str = "",
        transcript_excerpt: str = "",
        session_context: str = "",
    ) -> str:
        session_block = self._session_block(
            source_session_id=source_session_id,
            session_title=session_title,
            transcript_excerpt=transcript_excerpt,
            session_context=session_context,
        )
        return f"""You are revising an LABIT investigation report for project {project}.

Topic: {topic}
You should inspect the repository yourself as needed, but start by reading the previous draft from {prior_provider.value}. Improve correctness, fill gaps, tighten weak claims, and keep anything that is already solid.

Related prior reports:
{self._related_reports_block(related_reports)}

Current LABIT session context:
{session_block}

Previous draft:

{prior_report}

Return a complete final markdown report in the same structure as the draft. Keep the report evidence-driven and repository-grounded.
"""

    def _parallel_synthesis_prompt(
        self,
        *,
        project: str,
        topic: str,
        related_reports: list[InvestigationReportSummary],
        draft_a: str,
        draft_b: str,
        provider_a: ProviderKind,
        provider_b: ProviderKind,
        source_session_id: str = "",
        session_title: str = "",
        transcript_excerpt: str = "",
        session_context: str = "",
    ) -> str:
        session_block = self._session_block(
            source_session_id=source_session_id,
            session_title=session_title,
            transcript_excerpt=transcript_excerpt,
            session_context=session_context,
        )
        return f"""You are synthesizing two independent LABIT investigation drafts into one final report.

Project: {project}
Topic: {topic}

Related prior reports:
{self._related_reports_block(related_reports)}

Current LABIT session context:
{session_block}

Draft from {provider_a.value}:
{draft_a}

Draft from {provider_b.value}:
{draft_b}

Return one complete markdown report in the standard investigation format. Keep the strongest evidence-backed findings, remove duplication, and clearly preserve uncertainty where the drafts disagree.
"""

    def _related_reports_block(self, reports: list[InvestigationReportSummary]) -> str:
        if not reports:
            return "- None"
        lines: list[str] = []
        for item in reports[:5]:
            summary = item.summary or "(no summary)"
            lines.append(f"- {item.title} [{item.path}] :: {summary}")
        return "\n".join(lines)

    def _reports_dir(self, project: str) -> Path:
        return self.paths.vault_projects_dir / project / "docs" / "reports"

    def _parse_report(self, path: Path) -> InvestigationReportSummary:
        text = path.read_text(encoding="utf-8", errors="replace")
        title = self._extract_title(text) or path.stem
        return InvestigationReportSummary(
            title=title,
            path=str(path.relative_to(self.paths.root)),
            date=self._extract_field(text, "Date"),
            status=self._extract_field(text, "Status"),
            topic=self._extract_field(text, "Topic"),
            summary=self._extract_section(text, "Summary"),
        )

    def _write_report(self, *, project: str, title: str, markdown: str) -> Path:
        reports_dir = self._reports_dir(project)
        reports_dir.mkdir(parents=True, exist_ok=True)
        date_text = datetime.now(UTC).date().isoformat()
        slug = self._slugify(title)
        path = reports_dir / f"{date_text}_{slug}.md"
        index = 2
        while path.exists():
            path = reports_dir / f"{date_text}_{slug}-{index}.md"
            index += 1
        self._atomic_write(path, markdown.rstrip() + "\n")
        return path

    def _extract_title(self, markdown: str) -> str:
        for line in markdown.splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
        return ""

    def _extract_field(self, markdown: str, label: str) -> str:
        prefix = f"**{label}**:"
        for line in markdown.splitlines():
            if line.startswith(prefix):
                return line.split(":", 1)[1].strip()
        return ""

    def _extract_section(self, markdown: str, heading: str) -> str:
        pattern = rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, markdown)
        if not match:
            return ""
        text = match.group(1).strip()
        return " ".join(text.split())[:500]

    def _fallback_title(self, topic: str) -> str:
        compact = " ".join(topic.split())
        compact = compact[:80].strip()
        return compact.title() or "Investigation"

    def _tokenize(self, text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}

    def _overlap_score(self, a: set[str], b: set[str]) -> int:
        return len(a & b)

    def _slugify(self, text: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
        return slug or "investigation"

    def _session_block(
        self,
        *,
        source_session_id: str,
        session_title: str,
        transcript_excerpt: str,
        session_context: str,
    ) -> str:
        parts: list[str] = []
        if source_session_id.strip():
            parts.append(f"Session ID: {source_session_id.strip()}")
        if session_title.strip():
            parts.append(f"Session title: {session_title.strip()}")
        if transcript_excerpt.strip():
            parts.append("Recent transcript:\n" + transcript_excerpt.strip())
        if session_context.strip():
            parts.append("Bound context:\n" + session_context.strip())
        return "\n\n".join(parts) if parts else "- None"

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)

    def _extract_summary(self, markdown: str) -> str:
        return self._extract_section(markdown, "Summary")

    def _investigate_agent_extra_args(self, provider: ProviderKind) -> list[str]:
        if provider == ProviderKind.CLAUDE:
            return ["--effort", "low"]
        if provider == ProviderKind.CODEX:
            return ["-c", 'model_reasoning_effort="low"']
        return []

    def _investigate_allowed_tools(self, provider: ProviderKind) -> list[str]:
        if provider == ProviderKind.CLAUDE:
            return ["Read", "LS", "Glob", "Grep", "Bash"]
        return []
