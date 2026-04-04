from __future__ import annotations

import re
from dataclasses import dataclass

from labit.capture.models import CaptureRecord
from labit.capture.service import CaptureService
from labit.codebase.map import CodeMapBuilder
from labit.context.assembler import ContextSection
from labit.investigations.service import InvestigationService
from labit.papers.service import PaperService
from labit.paths import RepoPaths


@dataclass(frozen=True)
class _RankedPaper:
    paper_id: str
    title: str
    status: str
    path: str
    summary: str
    score: int


@dataclass(frozen=True)
class _RankedDoc:
    kind: str
    title: str
    path: str
    source: str
    excerpt: str
    score: int


class ContextMapBuilder:
    def __init__(self, paths: RepoPaths):
        self.paths = paths
        self.capture_service = CaptureService(paths)
        self.paper_service = PaperService(paths)
        self.investigation_service = InvestigationService(paths)
        self.code_map_builder = CodeMapBuilder(paths)

    def build_sections(
        self,
        *,
        project: str | None,
        query: str,
        evidence_refs: list[str] | None = None,
        exclude_paper_ids: list[str] | None = None,
    ) -> list[ContextSection]:
        if not project:
            return []

        sections: list[ContextSection] = []
        paper_section = self._build_paper_section(
            project=project,
            query=query,
            evidence_refs=evidence_refs or [],
            exclude_paper_ids=exclude_paper_ids or [],
        )
        if paper_section is not None:
            sections.append(paper_section)

        report_section = self._build_report_section(project=project, query=query)
        if report_section is not None:
            sections.append(report_section)

        docs_section = self._build_docs_section(project=project, query=query)
        if docs_section is not None:
            sections.append(docs_section)

        code_section = self._build_code_section(project=project, query=query)
        if code_section is not None:
            sections.append(code_section)

        return sections

    def shape_memory_query(self, *, base_query: str, sections: list[ContextSection], max_chars: int = 4000) -> str:
        parts: list[str] = []
        if base_query.strip():
            parts.append(base_query.strip())

        for section in sections:
            parts.append(f"[{section.title}]")
            lines = [line.strip() for line in section.content.splitlines() if line.strip()]
            parts.extend(lines[:8])

        shaped = "\n".join(parts).strip()
        if len(shaped) <= max_chars:
            return shaped
        return f"{shaped[: max_chars - 1].rstrip()}…"

    def _build_paper_section(
        self,
        *,
        project: str,
        query: str,
        evidence_refs: list[str],
        exclude_paper_ids: list[str],
    ) -> ContextSection | None:
        query_tokens = self._tokenize(query)
        exclude = set(exclude_paper_ids)
        evidence_papers = {ref.split(":", 1)[1] for ref in evidence_refs if ref.startswith("paper:")}
        ranked: list[_RankedPaper] = []

        for entry in self.paper_service.list_project_index_entries(project):
            if entry.paper_id in exclude:
                continue
            score = 0
            haystack = f"{entry.paper_id} {entry.title} {entry.status.value}"
            score += len(query_tokens & self._tokenize(haystack)) * 4
            if entry.paper_id in evidence_papers:
                score += 8
            if entry.status.value == "ingested":
                score += 2
            if score <= 0 and query_tokens:
                continue

            summary_excerpt = self._paper_summary_excerpt(project=project, paper_id=entry.paper_id)
            ranked.append(
                _RankedPaper(
                    paper_id=entry.paper_id,
                    title=entry.title,
                    status=entry.status.value,
                    path=entry.path,
                    summary=summary_excerpt,
                    score=score,
                )
            )

        if not ranked:
            fallback_entries = sorted(
                self.paper_service.list_project_index_entries(project),
                key=lambda entry: (entry.status.value == "ingested", entry.added_at),
                reverse=True,
            )
            for entry in fallback_entries[:4]:
                if entry.paper_id in exclude:
                    continue
                ranked.append(
                    _RankedPaper(
                        paper_id=entry.paper_id,
                        title=entry.title,
                        status=entry.status.value,
                        path=entry.path,
                        summary=self._paper_summary_excerpt(project=project, paper_id=entry.paper_id),
                        score=0,
                    )
                )

        if not ranked:
            return None

        ranked.sort(key=lambda item: (-item.score, item.title.lower()))
        lines: list[str] = []
        for item in ranked[:4]:
            lines.append(f"- {item.paper_id} | {item.status} | {item.title}")
            if item.summary:
                lines.append(f"  summary: {item.summary}")
            lines.append(f"  record: {item.path}")
        return ContextSection(
            title="Related Project Papers",
            source="map:papers",
            priority=60,
            content="\n".join(lines),
        )

    def _build_report_section(self, *, project: str, query: str) -> ContextSection | None:
        reports = self.investigation_service.find_related_reports(project, query, limit=4)
        if not reports:
            reports = self.investigation_service.list_reports(project)[:3]
        if not reports:
            return None

        lines: list[str] = []
        for report in reports:
            header = f"- {report.title}"
            if report.date:
                header = f"{header} ({report.date})"
            lines.append(header)
            if report.topic:
                lines.append(f"  topic: {report.topic}")
            if report.summary:
                lines.append(f"  summary: {self._clip(report.summary, 220)}")
            lines.append(f"  path: {report.path}")
        return ContextSection(
            title="Related Reports",
            source="map:reports",
            priority=58,
            content="\n".join(lines),
        )

    def _build_code_section(self, *, project: str, query: str) -> ContextSection | None:
        snapshot = self.code_map_builder.build_snapshot(project)
        if snapshot is None:
            return None
        relevant_paths = self.code_map_builder.build_relevant_paths(project, query=query)
        return ContextSection(
            title="Code Map",
            source="map:code",
            priority=56,
            content=self.code_map_builder.render_snapshot_with_relevant(snapshot, relevant_paths=relevant_paths),
        )

    def _build_docs_section(self, *, project: str, query: str) -> ContextSection | None:
        query_tokens = self._tokenize(query)
        ranked: list[_RankedDoc] = []
        for kind, records in (
            ("idea", self.capture_service.list_ideas(project)),
            ("note", self.capture_service.list_notes(project)),
            ("todo", self.capture_service.list_todos(project)),
        ):
            for record in records:
                excerpt = self._doc_excerpt(record)
                haystack = " ".join([record.title, record.source, excerpt, kind])
                score = len(query_tokens & self._tokenize(haystack)) * 4
                if score <= 0 and query_tokens:
                    continue
                ranked.append(
                    _RankedDoc(
                        kind=kind,
                        title=record.title,
                        path=record.path,
                        source=record.source,
                        excerpt=excerpt,
                        score=score,
                    )
                )

        if not ranked:
            fallback: list[_RankedDoc] = []
            for kind, records in (
                ("todo", self.capture_service.list_todos(project)),
                ("idea", self.capture_service.list_ideas(project)),
                ("note", self.capture_service.list_notes(project)),
            ):
                for record in records[:3]:
                    fallback.append(
                        _RankedDoc(
                            kind=kind,
                            title=record.title,
                            path=record.path,
                            source=record.source,
                            excerpt=self._doc_excerpt(record),
                            score=0,
                        )
                    )
            ranked = fallback

        if not ranked:
            return None

        ranked.sort(key=lambda item: (-item.score, item.kind, item.title.lower()))
        lines: list[str] = []
        for item in ranked[:5]:
            lines.append(f"- {item.kind} | {item.title}")
            if item.excerpt:
                lines.append(f"  excerpt: {item.excerpt}")
            lines.append(f"  path: {item.path}")
        return ContextSection(
            title="Related Docs",
            source="map:docs",
            priority=57,
            content="\n".join(lines),
        )

    def _paper_summary_excerpt(self, *, project: str, paper_id: str) -> str:
        try:
            record = self.paper_service.load_project_record(project, paper_id)
        except FileNotFoundError:
            return ""
        if not record.summary_path:
            return ""
        summary_path = self.paths.root / record.summary_path
        if not summary_path.exists():
            return ""
        text = summary_path.read_text(encoding="utf-8").strip()
        return self._clip(" ".join(text.split()), 260)

    def _doc_excerpt(self, record: CaptureRecord) -> str:
        path = self.paths.root / record.path
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8").strip()
        body_lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("# ") and not line.startswith("**")]
        return self._clip(" ".join(body_lines), 220)

    def _clip(self, text: str, limit: int) -> str:
        text = text.strip()
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1].rstrip()}…"

    def _tokenize(self, text: str) -> set[str]:
        return {token for token in re.findall(r"[a-zA-Z0-9_:-]+", text.lower()) if len(token) >= 3}
