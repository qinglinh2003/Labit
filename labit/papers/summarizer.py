from __future__ import annotations

from labit.agents.context import ContextBuilder
from labit.agents.models import AgentRequest, AgentRole, CollaborationMode, ProviderAssignment, SynthesisArtifact, TaskSpec
from labit.agents.orchestrator import AgentRuntime
from labit.agents.providers import resolve_provider_kind
from labit.papers.models import GlobalPaperMeta
from labit.papers.text import html_to_text
from labit.paths import RepoPaths


class PaperSummarizer:
    def __init__(self, paths: RepoPaths):
        self.paths = paths
        self.runtime = AgentRuntime(paths)
        self.context_builder = ContextBuilder(paths)

    def summarize(
        self,
        *,
        project: str,
        meta: GlobalPaperMeta,
        html_content: str | None,
        provider: str | None = None,
    ) -> tuple[str, str]:
        provider_kind = resolve_provider_kind(provider)
        excerpt = html_to_text(html_content) if html_content else ""
        if not excerpt:
            excerpt = meta.title
            if meta.url:
                excerpt += f"\nURL: {meta.url}"

        task = TaskSpec(
            kind="paper_ingest",
            goal=f"Summarize paper {meta.paper_id} for project {project}.",
            mode=CollaborationMode.DISCUSSION,
            requires_mutation=True,
            expected_outputs=["summary.md"],
            write_scope=[
                str(self.paths.vault_projects_dir / project / "key_papers" / meta.paper_id / "summary.md"),
            ],
            metadata={"project": project, "paper_id": meta.paper_id},
        )
        context = self.context_builder.build(task, project_name=project)
        manifest = self.runtime.begin_run(
            context,
            assignments=[ProviderAssignment(role=AgentRole.SYNTHESIZER, provider=provider_kind)],
        )

        prompt = self._build_prompt(meta, project=project, excerpt=excerpt)
        artifact = self.runtime.run_role(
            manifest,
            role=AgentRole.SYNTHESIZER,
            provider=provider_kind,
            request=AgentRequest(
                role=AgentRole.SYNTHESIZER,
                prompt=prompt,
                cwd=str(self.paths.root),
            ),
        )
        markdown = self._normalize_markdown(artifact.raw_output, meta)
        summary_target = self.paths.vault_projects_dir / project / "key_papers" / meta.paper_id / "summary.md"
        self.runtime.record_synthesis(
            manifest,
            SynthesisArtifact(
                run_id=manifest.run_id,
                summary=f"Generated project summary for {meta.paper_id} in {project}.",
                claims=[meta.title],
                evidence=["Metadata", "HTML excerpt" if html_content else "Metadata only"],
                recommended_next_step=f"Write summary.md for {meta.paper_id} under project {project}.",
                mutation_plan=[f"write {summary_target}"],
            ),
        )
        self.runtime.finish_run(manifest)
        return markdown, manifest.run_id

    def _build_prompt(self, meta: GlobalPaperMeta, *, project: str, excerpt: str) -> str:
        authors = ", ".join(meta.authors) or "Unknown"
        task = TaskSpec(
            kind="paper_ingest_context",
            goal=f"Build context for summarizing {meta.paper_id}.",
            mode=CollaborationMode.DISCUSSION,
        )
        context = self.context_builder.build(task, project_name=project)
        project_context = self._render_project_context(context)
        return f"""You are writing a project-specific research paper summary for LABIT.

Produce markdown only. Do not wrap in code fences.

The summary is for project `{project}`. It should help ongoing work in that project, not serve as a project-agnostic encyclopedia entry.

Use exactly this structure:

# {meta.title}

## TL;DR

## Core Idea

## Method

## Key Evidence

## Limitations

## Relevance To This Project

## Open Questions

Keep it concise, specific, and faithful to the provided material. If some details are unavailable, say so briefly instead of inventing them.

Metadata:
- Paper ID: {meta.paper_id}
- Title: {meta.title}
- Authors: {authors}
- Year: {meta.year or "Unknown"}
- Venue: {meta.venue or "Unknown"}
- URL: {meta.url or "Unknown"}

Current project context:
{project_context}

Source excerpt:
{excerpt}
"""

    def _render_project_context(self, context) -> str:
        parts: list[str] = []
        if context.project is not None:
            parts.append(f"- Project: {context.project.name}")
            if context.project.description:
                parts.append(f"- Project description: {context.project.description}")
            if context.project.keywords:
                parts.append(f"- Project keywords: {', '.join(context.project.keywords[:12])}")
            if context.project.relevance_criteria:
                parts.append(f"- Relevance criteria: {context.project.relevance_criteria}")
        if context.code is not None:
            parts.append(f"- Code root: {context.code.project_code_dir}")
            if context.code.package_roots:
                parts.append(f"- Package roots: {', '.join(context.code.package_roots)}")
            if context.code.entrypoints:
                parts.append(f"- Entrypoints: {', '.join(context.code.entrypoints[:6])}")
            if context.code.config_files:
                parts.append(f"- Config files: {', '.join(context.code.config_files[:6])}")
            if context.code.readme_excerpt:
                parts.append(f"- README excerpt:\n{context.code.readme_excerpt}")
        if context.memory.key_papers:
            titles = [paper.get("title", paper.get("paper_id", "")) for paper in context.memory.key_papers[:5]]
            parts.append(f"- Existing key papers: {', '.join([title for title in titles if title])}")
        if context.memory.open_hypotheses:
            ids = [item.get("id", "") for item in context.memory.open_hypotheses[:5]]
            parts.append(f"- Open hypotheses: {', '.join([item for item in ids if item])}")
        return "\n".join(parts) if parts else "- No additional project context available."

    def _normalize_markdown(self, output: str, meta: GlobalPaperMeta) -> str:
        text = output.strip()
        if not text:
            return self._fallback_summary(meta)
        if not text.startswith("# "):
            text = f"# {meta.title}\n\n{text}"
        return text.rstrip() + "\n"

    def _fallback_summary(self, meta: GlobalPaperMeta) -> str:
        return f"""# {meta.title}

## TL;DR
Summary generation returned no content.

## Core Idea
Unavailable.

## Method
Unavailable.

## Key Evidence
Unavailable.

## Limitations
Summary was generated from insufficient content.

## Relevance To This Project
Potentially relevant to: {", ".join(meta.relevance_to) or "unknown"}.

## Open Questions
- Review the raw paper assets manually.
"""
