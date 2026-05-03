from __future__ import annotations

import re
from dataclasses import dataclass

from labit.capture.models import CaptureRecord
from labit.capture.service import CaptureService
from labit.codebase.map import CodeMapBuilder
from labit.context.assembler import ContextSection
from labit.paths import RepoPaths


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
        self.code_map_builder = CodeMapBuilder(paths)

    def build_sections(
        self,
        *,
        project: str | None,
        query: str,
        evidence_refs: list[str] | None = None,
        allow_fallback: bool = False,
    ) -> list[ContextSection]:
        if not project:
            return []
        if not self._has_query_signal(query=query, evidence_refs=evidence_refs or []):
            return []

        sections: list[ContextSection] = []
        docs_section = self._build_docs_section(project=project, query=query, allow_fallback=allow_fallback)
        if docs_section is not None:
            sections.append(docs_section)

        code_section = self._build_code_section(project=project, query=query, allow_fallback=allow_fallback)
        if code_section is not None:
            sections.append(code_section)

        return sections

    def _has_query_signal(self, *, query: str, evidence_refs: list[str]) -> bool:
        if evidence_refs:
            return True
        return bool(self._tokenize(query))

    def _build_code_section(self, *, project: str, query: str, allow_fallback: bool) -> ContextSection | None:
        snapshot = self.code_map_builder.build_snapshot(project)
        if snapshot is None:
            return None
        relevant_paths = self.code_map_builder.build_relevant_paths(project, query=query, allow_fallback=allow_fallback)
        if not relevant_paths and not allow_fallback:
            return None
        return ContextSection(
            title="Code Map",
            source="map:code",
            priority=56,
            content=self.code_map_builder.render_snapshot_with_relevant(snapshot, relevant_paths=relevant_paths),
        )

    def _build_docs_section(self, *, project: str, query: str, allow_fallback: bool) -> ContextSection | None:
        query_tokens = self._tokenize(query)
        ranked: list[_RankedDoc] = []
        for kind, records in (
            ("idea", self.capture_service.list_ideas(project)),
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

        if not ranked and allow_fallback:
            fallback: list[_RankedDoc] = []
            for kind, records in (
                ("todo", self.capture_service.list_todos(project)),
                ("idea", self.capture_service.list_ideas(project)),
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
