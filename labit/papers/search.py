from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import re
from typing import Any

from labit.agents.adapters.base import AgentAdapterError
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
from labit.papers.arxiv import (
    ArxivClient,
    ArxivPaperSource,
    ArxivResolutionError,
    extract_arxiv_identifier,
    strip_arxiv_version,
)
from labit.papers.models import (
    DuplicateStatus,
    GlobalPaperMeta,
    PaperSearchCandidate,
    PaperSearchIntent,
    SearchMode,
    SearchScope,
)
from labit.papers.service import PaperService
from labit.paths import RepoPaths


def _discovery_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "queries": {"type": "array", "items": {"type": "string"}},
            "papers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "reference": {"type": "string"},
                        "title": {"type": "string"},
                        "why_relevant": {"type": "string"},
                    },
                    "required": ["reference", "title", "why_relevant"],
                    "additionalProperties": False,
                },
            },
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["queries", "papers", "notes"],
        "additionalProperties": False,
    }


def _ranking_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "paper_id": {"type": "string"},
                        "one_line_description": {"type": "string"},
                        "why_relevant": {"type": "string"},
                        "score": {"type": "number"},
                    },
                    "required": ["paper_id", "one_line_description", "why_relevant", "score"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["results"],
        "additionalProperties": False,
    }


class PaperSearchService:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        arxiv_client: ArxivClient | None = None,
        paper_service: PaperService | None = None,
        runtime: AgentRuntime | None = None,
        context_builder: ContextBuilder | None = None,
    ):
        self.paths = paths
        self.arxiv_client = arxiv_client or ArxivClient()
        self.paper_service = paper_service or PaperService(paths)
        self.runtime = runtime or AgentRuntime(paths)
        self.context_builder = context_builder or ContextBuilder(paths)

    def search(
        self,
        *,
        project: str,
        intent: PaperSearchIntent,
        provider: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if intent.mode == SearchMode.DISCUSSION:
            return self._discussion_search(project=project, intent=intent, progress=progress)
        return self._single_search(project=project, intent=intent, provider=provider, progress=progress)

    def _single_search(
        self,
        *,
        project: str,
        intent: PaperSearchIntent,
        provider: str | None,
        progress: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        provider_kind = resolve_provider_kind(provider)
        self._notify(progress, "Building project-aware search context")
        task = TaskSpec(
            kind="paper_search",
            goal=f"Find papers for project {project}: {intent.query}",
            mode=CollaborationMode.DISCUSSION,
            expected_outputs=["ranked_candidates"],
            metadata={"project": project, "query": intent.query},
        )
        context = self.context_builder.build(task, project_name=project)
        manifest = self.runtime.begin_run(
            context,
            assignments=[
                ProviderAssignment(role=AgentRole.SCOUT, provider=provider_kind),
                ProviderAssignment(role=AgentRole.SYNTHESIZER, provider=provider_kind),
            ],
        )

        self._notify(progress, f"Running agent discovery with {provider_kind.value}")
        try:
            discovery_artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.SCOUT,
                provider=provider_kind,
                request=AgentRequest(
                    role=AgentRole.SCOUT,
                    prompt=self._build_discovery_prompt(project=project, intent=intent),
                    output_schema=_discovery_schema(),
                    cwd=str(self.paths.root),
                    timeout_seconds=60,
                    extra_args=self._search_agent_extra_args(provider_kind),
                ),
            )
            discovery = self._extract_discovery(
                discovery_artifact.output,
                fallback=intent.query,
                source_label=f"agent:{provider_kind.value}",
            )
        except AgentAdapterError as exc:
            self._notify(progress, f"Agent discovery unavailable, falling back to backend-only ({exc})")
            discovery = {
                "queries": [intent.query],
                "papers": [],
                "notes": [f"Scout fallback: {exc}"],
            }
        self._notify(
            progress,
            f"Running hybrid retrieval for {len(discovery['queries'])} backend queries and {len(discovery['papers'])} agent papers",
        )
        candidates = self._retrieve_hybrid_candidates(
            project=project,
            queries=discovery["queries"],
            agent_papers=discovery["papers"],
            limit=max(intent.limit * 2, 10),
            scope=intent.scope,
        )
        self._notify(progress, f"Ranking {len(candidates)} combined candidates with {provider_kind.value}")
        ranked = self._rank_candidates(
            manifest=manifest,
            project=project,
            intent=intent,
            candidates=candidates,
            provider=provider_kind,
        )
        self.runtime.record_synthesis(
            manifest,
            SynthesisArtifact(
                run_id=manifest.run_id,
                summary=f"Ranked {len(ranked)} search candidates for project {project}.",
                claims=[candidate.title for candidate in ranked[:3]],
                evidence=[
                    f"Backend queries: {', '.join(discovery['queries'][:3])}",
                    f"Agent-discovered papers: {len(discovery['papers'])}",
                ],
                recommended_next_step="Review ranked candidates and select pull or ingest.",
            ),
        )
        self._notify(progress, "Finalizing search run")
        self.runtime.finish_run(manifest)
        return {
            "run_id": manifest.run_id,
            "mode": intent.mode.value,
            "project": project,
            "queries": discovery["queries"],
            "agent_references": [item["reference"] for item in discovery["papers"]],
            "notes": discovery["notes"],
            "results": [candidate.model_dump(mode="json") for candidate in ranked[: intent.limit]],
        }

    def _discussion_search(
        self,
        *,
        project: str,
        intent: PaperSearchIntent,
        progress: Callable[[str], None] | None,
    ) -> dict[str, Any]:
        scout_provider, normalizer_provider = discussion_provider_kinds()
        self._notify(progress, "Building project-aware search context")
        task = TaskSpec(
            kind="paper_search",
            goal=f"Discuss and find papers for project {project}: {intent.query}",
            mode=CollaborationMode.DISCUSSION,
            expected_outputs=["ranked_candidates"],
            metadata={"project": project, "query": intent.query},
        )
        context = self.context_builder.build(task, project_name=project)
        manifest = self.runtime.begin_run(
            context,
            assignments=[
                ProviderAssignment(role=AgentRole.SCOUT, provider=scout_provider),
                ProviderAssignment(role=AgentRole.NORMALIZER, provider=normalizer_provider),
                ProviderAssignment(role=AgentRole.SYNTHESIZER, provider=scout_provider),
            ],
        )

        self._notify(progress, f"Scout discovery with {scout_provider.value}")
        try:
            scout_artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.SCOUT,
                provider=scout_provider,
                request=AgentRequest(
                    role=AgentRole.SCOUT,
                    prompt=self._build_discovery_prompt(project=project, intent=intent),
                    output_schema=_discovery_schema(),
                    cwd=str(self.paths.root),
                    timeout_seconds=60,
                    extra_args=self._search_agent_extra_args(scout_provider),
                ),
            )
            scout_discovery = self._extract_discovery(
                scout_artifact.output,
                fallback=intent.query,
                source_label=f"agent:{scout_provider.value}",
            )
        except AgentAdapterError as exc:
            self._notify(progress, f"Scout unavailable, falling back to backend-first search ({exc})")
            scout_discovery = {
                "queries": [intent.query],
                "papers": [],
                "notes": [f"Scout fallback: {exc}"],
            }

        self._notify(progress, f"Normalizer discovery with {normalizer_provider.value}")
        try:
            normalizer_artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.NORMALIZER,
                provider=normalizer_provider,
                request=AgentRequest(
                    role=AgentRole.NORMALIZER,
                    prompt=self._build_normalizer_prompt(
                        project=project,
                        intent=intent,
                        scout_queries=scout_discovery["queries"],
                        scout_papers=scout_discovery["papers"],
                    ),
                    output_schema=_discovery_schema(),
                    cwd=str(self.paths.root),
                    timeout_seconds=60,
                    extra_args=self._search_agent_extra_args(normalizer_provider),
                ),
            )
            normalizer_discovery = self._extract_discovery(
                normalizer_artifact.output,
                fallback=intent.query,
                source_label=f"agent:{normalizer_provider.value}",
            )
        except AgentAdapterError as exc:
            self._notify(progress, f"Normalizer unavailable, falling back to scout-only ({exc})")
            normalizer_discovery = {
                "queries": [],
                "papers": [],
                "notes": [f"Normalizer fallback: {exc}"],
            }

        queries = self._merge_strings(
            normalizer_discovery["queries"],
            scout_discovery["queries"],
            fallback=intent.query,
            limit=4,
        )
        agent_papers = self._merge_agent_papers(
            normalizer_discovery["papers"],
            scout_discovery["papers"],
            limit=8,
        )
        notes = self._merge_strings(
            normalizer_discovery["notes"],
            scout_discovery["notes"],
            fallback="",
            limit=8,
            allow_empty=False,
        )
        self._notify(
            progress,
            f"Running hybrid retrieval for {len(queries)} backend queries and {len(agent_papers)} agent papers",
        )
        candidates = self._retrieve_hybrid_candidates(
            project=project,
            queries=queries,
            agent_papers=agent_papers,
            limit=max(intent.limit * 3, 12),
            scope=intent.scope,
        )
        self._notify(progress, f"Ranking {len(candidates)} combined candidates with {scout_provider.value}")
        ranked = self._rank_candidates(
            manifest=manifest,
            project=project,
            intent=intent,
            candidates=candidates,
            provider=scout_provider,
        )
        self.runtime.record_synthesis(
            manifest,
            SynthesisArtifact(
                run_id=manifest.run_id,
                summary=f"Discussed and ranked {len(ranked)} candidates for project {project}.",
                claims=[candidate.title for candidate in ranked[:3]],
                evidence=[
                    f"Scout queries: {', '.join(scout_discovery['queries'][:3])}",
                    f"Final queries: {', '.join(queries[:3])}",
                    f"Agent-discovered papers: {len(agent_papers)}",
                ],
                recommended_next_step="Review discussion-ranked candidates and select pull or ingest.",
            ),
        )
        self._notify(progress, "Finalizing search run")
        self.runtime.finish_run(manifest)
        return {
            "run_id": manifest.run_id,
            "mode": intent.mode.value,
            "project": project,
            "queries": queries,
            "agent_references": [item["reference"] for item in agent_papers],
            "notes": notes,
            "results": [candidate.model_dump(mode="json") for candidate in ranked[: intent.limit]],
        }

    def _retrieve_hybrid_candidates(
        self,
        *,
        project: str,
        queries: list[str],
        agent_papers: list[dict[str, Any]],
        limit: int,
        scope: SearchScope,
    ) -> list[PaperSearchCandidate]:
        with ThreadPoolExecutor(max_workers=2) as executor:
            backend_future = executor.submit(
                self._fetch_backend_candidates,
                project=project,
                queries=queries,
                limit=limit,
                scope=scope,
            )
            agent_future = executor.submit(
                self._resolve_agent_candidates,
                project=project,
                papers=agent_papers,
            )
            backend_candidates = backend_future.result()
            agent_candidates = agent_future.result()

        return self._merge_candidates(agent_candidates, backend_candidates)

    def _fetch_backend_candidates(
        self,
        *,
        project: str,
        queries: list[str],
        limit: int,
        scope: SearchScope,
    ) -> list[PaperSearchCandidate]:
        merged: OrderedDict[str, PaperSearchCandidate] = OrderedDict()
        sort_by = "lastUpdatedDate" if scope == SearchScope.BROAD else "relevance"
        per_query = min(max(limit, 6), 12)
        for query in queries:
            for source in self.arxiv_client.search(query, max_results=per_query, sort_by=sort_by):
                candidate = self._candidate_from_source(
                    source,
                    project=project,
                    retrieval_source="backend:arxiv",
                )
                if candidate.paper_id not in merged:
                    merged[candidate.paper_id] = candidate
                else:
                    merged[candidate.paper_id] = self._merge_candidate(merged[candidate.paper_id], candidate)
                if len(merged) >= limit:
                    break
            if len(merged) >= limit:
                break
        return list(merged.values())

    def _resolve_agent_candidates(
        self,
        *,
        project: str,
        papers: list[dict[str, Any]],
    ) -> list[PaperSearchCandidate]:
        merged: OrderedDict[str, PaperSearchCandidate] = OrderedDict()
        for item in papers:
            reference = item.get("reference", "").strip()
            if not reference:
                continue
            try:
                source = self.arxiv_client.resolve(reference)
            except ArxivResolutionError:
                continue

            candidate = self._candidate_from_source(
                source,
                project=project,
                retrieval_source="agent",
                one_line_description=item.get("title", "").strip(),
                why_relevant=item.get("why_relevant", "").strip(),
            )
            source_labels = [str(label).strip() for label in item.get("source_labels", []) if str(label).strip()]
            if source_labels:
                candidate = candidate.model_copy(update={"retrieval_sources": source_labels})
            if candidate.paper_id not in merged:
                merged[candidate.paper_id] = candidate
            else:
                merged[candidate.paper_id] = self._merge_candidate(merged[candidate.paper_id], candidate)
        return list(merged.values())

    def _candidate_from_source(
        self,
        source: ArxivPaperSource,
        *,
        project: str,
        retrieval_source: str,
        one_line_description: str = "",
        why_relevant: str = "",
    ) -> PaperSearchCandidate:
        meta = GlobalPaperMeta(
            paper_id=source.canonical_paper_id,
            title=source.title,
            authors=source.authors,
            year=source.year,
            source="arXiv",
            url=source.abs_url,
            html_url=source.html_url,
            pdf_url=source.pdf_url,
            external_ids={"arxiv": source.arxiv_id},
        )
        duplicate = self.paper_service.find_duplicate(meta, project=project)
        return PaperSearchCandidate(
            paper_id=source.canonical_paper_id,
            arxiv_id=source.arxiv_id,
            title=source.title,
            authors=source.authors,
            year=source.year,
            abstract=source.abstract,
            url=source.abs_url,
            html_url=source.html_url,
            pdf_url=source.pdf_url,
            one_line_description=one_line_description,
            why_relevant=why_relevant,
            duplicate_status=duplicate.status,
            duplicate_reason=duplicate.reason,
            retrieval_sources=[retrieval_source],
        )

    def _rank_candidates(
        self,
        *,
        manifest,
        project: str,
        intent: PaperSearchIntent,
        candidates: list[PaperSearchCandidate],
        provider: ProviderKind,
    ) -> list[PaperSearchCandidate]:
        if not candidates:
            return []

        request = AgentRequest(
            role=AgentRole.SYNTHESIZER,
            prompt=self._build_ranking_prompt(project=project, intent=intent, candidates=candidates),
            output_schema=_ranking_schema(),
            cwd=str(self.paths.root),
            timeout_seconds=60,
            extra_args=self._search_agent_extra_args(provider),
        )
        try:
            artifact = self.runtime.run_role(
                manifest,
                role=AgentRole.SYNTHESIZER,
                provider=provider,
                request=request,
            )
        except AgentAdapterError:
            return self._heuristic_rank_candidates(candidates, intent=intent)
        ranked_data = self._extract_rankings(artifact.output)
        by_id = {candidate.paper_id: candidate for candidate in candidates}
        ranked: list[PaperSearchCandidate] = []
        seen: set[str] = set()
        for idx, item in enumerate(ranked_data, start=1):
            paper_id = item.get("paper_id")
            if not paper_id or paper_id not in by_id or paper_id in seen:
                continue
            candidate = by_id[paper_id].model_copy(
                update={
                    "one_line_description": item.get("one_line_description", "").strip(),
                    "why_relevant": item.get("why_relevant", "").strip(),
                    "score": float(item.get("score", 0)),
                    "rank": idx,
                }
            )
            ranked.append(candidate)
            seen.add(paper_id)

        for candidate in candidates:
            if candidate.paper_id in seen:
                continue
            ranked.append(
                candidate.model_copy(
                    update={
                        "one_line_description": candidate.one_line_description or self._default_description(candidate.abstract),
                        "why_relevant": candidate.why_relevant or self._default_relevance_reason(candidate, intent),
                        "rank": len(ranked) + 1,
                    }
                )
            )
        return ranked

    def _heuristic_rank_candidates(
        self,
        candidates: list[PaperSearchCandidate],
        *,
        intent: PaperSearchIntent | None,
    ) -> list[PaperSearchCandidate]:
        duplicate_priority = {
            DuplicateStatus.NEW: 3,
            DuplicateStatus.IN_GLOBAL: 2,
            DuplicateStatus.IN_PROJECT: 1,
            DuplicateStatus.IN_GLOBAL_AND_PROJECT: 0,
        }
        ranked: list[PaperSearchCandidate] = []
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                -len(candidate.retrieval_sources),
                -duplicate_priority.get(candidate.duplicate_status, 0),
                -(candidate.year or 0),
                candidate.title.lower(),
            ),
        )
        for idx, candidate in enumerate(ordered, start=1):
            ranked.append(
                candidate.model_copy(
                    update={
                        "one_line_description": candidate.one_line_description or self._default_description(candidate.abstract),
                        "why_relevant": candidate.why_relevant or self._default_relevance_reason(candidate, intent),
                        "rank": idx,
                    }
                )
            )
        return ranked

    def _merge_candidates(self, *groups: list[PaperSearchCandidate]) -> list[PaperSearchCandidate]:
        merged: OrderedDict[str, PaperSearchCandidate] = OrderedDict()
        for group in groups:
            for candidate in group:
                existing = merged.get(candidate.paper_id)
                if existing is None:
                    merged[candidate.paper_id] = candidate
                    continue
                merged[candidate.paper_id] = self._merge_candidate(existing, candidate)
        return list(merged.values())

    def _merge_candidate(self, existing: PaperSearchCandidate, incoming: PaperSearchCandidate) -> PaperSearchCandidate:
        retrieval_sources = existing.retrieval_sources + [
            item for item in incoming.retrieval_sources if item not in existing.retrieval_sources
        ]
        return existing.model_copy(
            update={
                "one_line_description": existing.one_line_description or incoming.one_line_description,
                "why_relevant": existing.why_relevant or incoming.why_relevant,
                "retrieval_sources": retrieval_sources,
            }
        )

    def _extract_discovery(
        self,
        output: Any,
        *,
        fallback: str,
        source_label: str,
    ) -> dict[str, Any]:
        raw_queries: list[str] = []
        raw_notes: list[str] = []
        papers: list[dict[str, Any]] = []

        if isinstance(output, dict):
            raw_queries = [str(query).strip() for query in (output.get("queries") or []) if str(query).strip()]
            raw_notes = [str(note).strip() for note in (output.get("notes") or []) if str(note).strip()]
            for item in output.get("papers") or []:
                if not isinstance(item, dict):
                    continue
                reference = str(item.get("reference", "")).strip()
                title = str(item.get("title", "")).strip()
                why_relevant = str(item.get("why_relevant", "")).strip()
                if not reference:
                    continue
                papers.append(
                    {
                        "reference": reference,
                        "title": title,
                        "why_relevant": why_relevant,
                        "source_labels": [source_label],
                    }
                )

        return {
            "queries": self._merge_strings(raw_queries, fallback=fallback, limit=4),
            "papers": self._merge_agent_papers(papers, limit=6),
            "notes": self._merge_strings(raw_notes, fallback="", limit=8, allow_empty=False),
        }

    def _extract_rankings(self, output: Any) -> list[dict[str, Any]]:
        if isinstance(output, dict):
            results = output.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
        return []

    def _merge_strings(
        self,
        *groups: list[str],
        fallback: str,
        limit: int,
        allow_empty: bool = True,
    ) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for item in group:
                value = item.strip()
                if not value:
                    continue
                key = value.lower()
                if key in seen:
                    continue
                merged.append(value)
                seen.add(key)
                if len(merged) >= limit:
                    return merged
        if merged:
            return merged
        if fallback or allow_empty:
            return [fallback] if fallback else []
        return []

    def _merge_agent_papers(self, *groups: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        by_key: dict[str, dict[str, Any]] = {}
        for group in groups:
            for item in group:
                reference = item.get("reference", "").strip()
                if not reference:
                    continue
                identifier = extract_arxiv_identifier(reference) or reference
                normalized_identifier = strip_arxiv_version(identifier) if extract_arxiv_identifier(reference) else identifier
                key = normalized_identifier.lower()
                existing = by_key.get(key)
                if existing is not None:
                    source_labels = existing.get("source_labels", [])
                    for label in item.get("source_labels", []):
                        if label not in source_labels:
                            source_labels.append(label)
                    existing["source_labels"] = source_labels
                    if not existing.get("title") and item.get("title"):
                        existing["title"] = item["title"]
                    if not existing.get("why_relevant") and item.get("why_relevant"):
                        existing["why_relevant"] = item["why_relevant"]
                    continue
                merged_item = {
                    "reference": reference,
                    "title": item.get("title", ""),
                    "why_relevant": item.get("why_relevant", ""),
                    "source_labels": list(item.get("source_labels", [])),
                }
                merged.append(merged_item)
                by_key[key] = merged_item
                if len(merged) >= limit:
                    return merged
        return merged

    def _build_discovery_prompt(self, *, project: str, intent: PaperSearchIntent) -> str:
        focus = intent.focus or "(no extra focus provided)"
        return f"""You are helping search papers for project `{project}`.

User search intent:
- Query: {intent.query}
- Priority: {focus}
- Scope: {intent.scope.value}
- Desired results: {intent.limit}

Use your search tools if available. Search for concrete arXiv papers, not just keyword variants.

Return:
- 2 to 4 concise arXiv-friendly queries in `queries`
- 2 to 6 concrete arXiv papers in `papers`
- brief notes in `notes`

For each `papers` item, include:
- `reference`: an arXiv id or arXiv URL
- `title`: the paper title
- `why_relevant`: one sentence on why it matters to this project

Bias toward ML / vision-language / reinforcement learning terminology when helpful.
Prefer papers that look actionable for the active project.
"""

    def _build_normalizer_prompt(
        self,
        *,
        project: str,
        intent: PaperSearchIntent,
        scout_queries: list[str],
        scout_papers: list[dict[str, str]],
    ) -> str:
        scout_lines = [
            f"- {item['reference']}: {item['title']} ({item['why_relevant']})"
            for item in scout_papers[:6]
        ]
        return f"""Refine the paper search plan for project `{project}`.

Original intent:
- Query: {intent.query}
- Priority: {intent.focus or "(none)"}
- Scope: {intent.scope.value}

Scout queries:
- {'; '.join(scout_queries)}

Scout papers:
{chr(10).join(scout_lines) or '- (none)'}

Search independently using your own tools if available, then refine the overall search.

Return:
- 2 to 4 final search queries for backend arXiv retrieval
- 2 to 6 concrete arXiv papers worth considering
- brief notes on what you changed

Remove redundant or low-signal queries and papers. Add missing high-signal papers if you find better ones.
"""

    def _build_ranking_prompt(
        self,
        *,
        project: str,
        intent: PaperSearchIntent,
        candidates: list[PaperSearchCandidate],
    ) -> str:
        lines: list[str] = []
        for candidate in candidates:
            authors = ", ".join(candidate.authors[:4])
            lines.append(
                f"- paper_id: {candidate.paper_id}\n"
                f"  title: {candidate.title}\n"
                f"  year: {candidate.year or 'Unknown'}\n"
                f"  authors: {authors or 'Unknown'}\n"
                f"  abstract: {candidate.abstract[:600]}\n"
                f"  duplicate_status: {candidate.duplicate_status.value}\n"
                f"  retrieval_sources: {', '.join(candidate.retrieval_sources) or 'unknown'}\n"
                f"  agent_hint: {candidate.why_relevant or '(none)'}"
            )

        return f"""Rank these paper candidates for project `{project}`.

Search intent:
- Query: {intent.query}
- Priority: {intent.focus or "(none)"}
- Scope: {intent.scope.value}

For each selected result, provide:
- a one-line description
- why it is relevant to the project
- a numeric score from 0 to 10

Prefer papers that are practically useful to the active project, not just loosely related.

Candidates:
{chr(10).join(lines)}
"""

    def _default_description(self, abstract: str) -> str:
        if not abstract:
            return "No summary available."
        sentence = abstract.split(".")[0].strip()
        return sentence + ("." if sentence and not sentence.endswith(".") else "")

    def _default_relevance_reason(
        self,
        candidate: PaperSearchCandidate,
        intent: PaperSearchIntent | None,
    ) -> str:
        terms = self._matched_terms(candidate, intent)
        parts: list[str] = []
        if terms:
            parts.append(f"Matches search terms: {', '.join(terms[:4])}.")
        else:
            parts.append("Title and abstract are close to the current project search intent.")

        sources = self._format_retrieval_sources(candidate.retrieval_sources)
        if sources:
            parts.append(f"Found via {sources}.")

        if candidate.duplicate_status == DuplicateStatus.NEW:
            parts.append("This paper is not already in the project library.")
        elif candidate.duplicate_status == DuplicateStatus.IN_GLOBAL:
            parts.append("This paper is already in the global library but not yet linked to this project.")
        elif candidate.duplicate_status == DuplicateStatus.IN_PROJECT:
            parts.append("This paper is already linked to this project.")
        elif candidate.duplicate_status == DuplicateStatus.IN_GLOBAL_AND_PROJECT:
            parts.append("This paper is already linked to both the global library and this project.")

        return " ".join(parts)

    def _matched_terms(
        self,
        candidate: PaperSearchCandidate,
        intent: PaperSearchIntent | None,
    ) -> list[str]:
        candidate_text = f"{candidate.title} {candidate.abstract}".lower()
        query_text = ""
        if intent is not None:
            query_text = f"{intent.query} {intent.focus}".lower()
        else:
            query_text = candidate.title.lower()

        stopwords = {
            "about",
            "from",
            "into",
            "that",
            "this",
            "with",
            "using",
            "their",
            "what",
            "should",
            "prioritize",
            "paper",
            "papers",
            "model",
        }
        matched: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"[a-z0-9\-\+]+", query_text):
            if len(token) < 3 and token not in {"rl", "vlm"}:
                continue
            if token in stopwords or token in seen:
                continue
            if token in candidate_text:
                matched.append(token)
                seen.add(token)
        return matched

    def _format_retrieval_sources(self, sources: list[str]) -> str:
        labels: list[str] = []
        mapping = {
            "backend:arxiv": "backend arXiv search",
            "agent:claude": "Claude discovery",
            "agent:codex": "Codex discovery",
        }
        for source in sources:
            label = mapping.get(source, source)
            if label not in labels:
                labels.append(label)
        if not labels:
            return ""
        if len(labels) == 1:
            return labels[0]
        if len(labels) == 2:
            return f"{labels[0]} and {labels[1]}"
        return f"{', '.join(labels[:-1])}, and {labels[-1]}"

    def _notify(self, progress: Callable[[str], None] | None, message: str) -> None:
        if progress is not None:
            progress(message)

    def _search_agent_extra_args(self, provider: ProviderKind) -> list[str]:
        if provider == ProviderKind.CLAUDE:
            return ["--effort", "low"]
        if provider == ProviderKind.CODEX:
            return ["-c", 'model_reasoning_effort="low"']
        return []
