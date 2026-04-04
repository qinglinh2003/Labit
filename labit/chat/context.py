from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from labit.chat.models import (
    ChatMessage,
    ChatSession,
    ContextBinding,
    ContextBlock,
    ContextSnapshot,
    MemoryBinding,
    MemoryBlock,
)
from labit.context.store import SessionContextStore
from labit.paths import RepoPaths
from labit.papers.service import PaperService
from labit.papers.text import html_to_text


class ConversationContextProvider(ABC):
    name: str

    @abstractmethod
    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: ContextBinding,
        paths: RepoPaths,
    ) -> list[ContextBlock]:
        raise NotImplementedError


class ConversationMemoryProvider(ABC):
    name: str

    @abstractmethod
    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: MemoryBinding,
        paths: RepoPaths,
    ) -> list[MemoryBlock]:
        raise NotImplementedError


class EmptyContextProvider(ConversationContextProvider):
    name = "none"

    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: ContextBinding,
        paths: RepoPaths,
    ) -> list[ContextBlock]:
        return []


class EmptyMemoryProvider(ConversationMemoryProvider):
    name = "none"

    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: MemoryBinding,
        paths: RepoPaths,
    ) -> list[MemoryBlock]:
        return []


class SessionWorkingMemoryProvider(ConversationMemoryProvider):
    name = "session_working_memory"

    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: MemoryBinding,
        paths: RepoPaths,
    ) -> list[MemoryBlock]:
        store = SessionContextStore(paths)
        snapshot = store.load_working_memory(session.session_id)
        if snapshot is None:
            return []

        lines: list[str] = []
        if snapshot.current_goal:
            lines.append(f"Current goal: {snapshot.current_goal}")
        if snapshot.active_artifacts:
            lines.append(f"Active artifacts: {', '.join(snapshot.active_artifacts)}")
        if snapshot.decisions_made:
            lines.append("Decisions:")
            lines.extend(f"- {item}" for item in snapshot.decisions_made)
        if snapshot.open_questions:
            lines.append("Open questions:")
            lines.extend(f"- {item}" for item in snapshot.open_questions)
        if snapshot.discussion_state.consensus:
            lines.append("Consensus:")
            lines.extend(f"- {item}" for item in snapshot.discussion_state.consensus)
        if snapshot.discussion_state.disagreements:
            lines.append("Disagreements:")
            lines.extend(f"- {item}" for item in snapshot.discussion_state.disagreements)
        if snapshot.followups:
            lines.append("Follow-ups:")
            lines.extend(f"- {item}" for item in snapshot.followups)
        if snapshot.evidence_refs:
            lines.append("Evidence refs:")
            lines.extend(f"- {item}" for item in snapshot.evidence_refs)
        if not lines:
            return []

        return [
            MemoryBlock(
                source=self.name,
                title="Session Working Memory",
                content="\n".join(lines),
            )
        ]


class PaperFocusContextProvider(ConversationContextProvider):
    name = "paper_focus"

    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: ContextBinding,
        paths: RepoPaths,
    ) -> list[ContextBlock]:
        paper_id = str(binding.config.get("paper_id", "")).strip()
        if not paper_id:
            raise ValueError("paper_focus context requires config.paper_id.")

        service = PaperService(paths)
        global_record = service.load_global_record(paper_id)
        project = session.project
        excerpt_chars = self._coerce_excerpt_chars(binding.config.get("excerpt_chars"))

        blocks: list[ContextBlock] = []
        blocks.append(
            ContextBlock(
                source=self.name,
                title=f"Paper Metadata · {paper_id}",
                content=self._render_metadata(service, global_record.meta, project=project),
            )
        )

        if project:
            try:
                project_record = service.load_project_record(project, paper_id)
            except FileNotFoundError:
                project_record = None

            if project_record is not None:
                blocks.append(
                    ContextBlock(
                        source=self.name,
                        title=f"Project Paper Record · {project}",
                        content=self._render_project_record(project_record),
                    )
                )

                if project_record.summary_path:
                    summary_path = paths.root / project_record.summary_path
                    if summary_path.exists():
                        blocks.append(
                            ContextBlock(
                                source=self.name,
                                title=f"Project Summary · {project}",
                                content=summary_path.read_text(encoding="utf-8").strip(),
                            )
                        )

                if project_record.notes_path:
                    notes_path = paths.root / project_record.notes_path
                    if notes_path.exists():
                        blocks.append(
                            ContextBlock(
                                source=self.name,
                                title=f"Project Notes · {project}",
                                content=notes_path.read_text(encoding="utf-8").strip(),
                            )
                        )

        excerpt = self._read_source_excerpt(global_record.html_path, max_chars=excerpt_chars)
        if excerpt:
            blocks.append(
                ContextBlock(
                    source=self.name,
                    title=f"Paper Source Excerpt · {paper_id}",
                    content=excerpt,
                )
            )
        else:
            blocks.append(
                ContextBlock(
                    source=self.name,
                    title=f"Paper Assets · {paper_id}",
                    content=self._render_asset_paths(global_record.html_path, global_record.pdf_path),
                )
            )

        return blocks

    def _coerce_excerpt_chars(self, value: object) -> int:
        if value is None:
            return 20000
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 20000
        return max(2000, min(parsed, 80000))

    def _render_metadata(self, service: PaperService, meta, *, project: str | None) -> str:
        authors = ", ".join(meta.authors) or "(unknown)"
        lines = [
            f"Paper ID: {meta.paper_id}",
            f"Title: {meta.title}",
            f"Authors: {authors}",
            f"Year: {meta.year or '(unknown)'}",
            f"Venue: {meta.venue or '(blank)'}",
            f"Source: {meta.source or '(blank)'}",
            f"URL: {meta.url or '(none)'}",
            f"HTML URL: {meta.html_url or '(none)'}",
            f"PDF URL: {meta.pdf_url or '(none)'}",
            f"Linked projects: {', '.join(meta.relevance_to) or '(none)'}",
        ]
        if project:
            lines.append(f"Active project: {project}")
        return "\n".join(lines)

    def _render_project_record(self, record) -> str:
        lines = [
            f"Project: {record.project}",
            f"Status: {record.status.value}",
            f"Global dir: {record.global_dir}",
            f"Metadata path: {record.meta_path}",
            f"HTML path: {record.html_path or '(none)'}",
            f"PDF path: {record.pdf_path or '(none)'}",
            f"Project summary: {record.summary_path or '(none)'}",
            f"Project notes: {record.notes_path or '(none)'}",
            f"Added: {record.added_at}",
            f"Updated: {record.updated_at}",
        ]
        return "\n".join(lines)

    def _render_asset_paths(self, html_path: str | None, pdf_path: str | None) -> str:
        lines = [
            f"HTML path: {html_path or '(none)'}",
            f"PDF path: {pdf_path or '(none)'}",
        ]
        return "\n".join(lines)

    def _read_source_excerpt(self, html_path: str | None, *, max_chars: int) -> str:
        if not html_path:
            return ""
        path = Path(html_path)
        if not path.exists():
            return ""
        try:
            html = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ""
        return html_to_text(html, max_chars=max_chars)


@dataclass
class ConversationContextRegistry:
    context_providers: dict[str, ConversationContextProvider]
    memory_providers: dict[str, ConversationMemoryProvider]

    @classmethod
    def default(cls) -> "ConversationContextRegistry":
        return cls(
            context_providers={
                EmptyContextProvider.name: EmptyContextProvider(),
                PaperFocusContextProvider.name: PaperFocusContextProvider(),
            },
            memory_providers={
                EmptyMemoryProvider.name: EmptyMemoryProvider(),
                SessionWorkingMemoryProvider.name: SessionWorkingMemoryProvider(),
            },
        )

    def build_snapshot(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        paths: RepoPaths,
    ) -> ContextSnapshot:
        context_blocks: list[ContextBlock] = []
        memory_blocks: list[MemoryBlock] = []

        for binding in session.context_bindings:
            provider = self.context_providers.get(binding.provider)
            if provider is None:
                raise KeyError(f"Unknown context provider '{binding.provider}'.")
            context_blocks.extend(
                provider.build(session=session, transcript=transcript, binding=binding, paths=paths)
            )

        for binding in session.memory_bindings:
            provider = self.memory_providers.get(binding.provider)
            if provider is None:
                raise KeyError(f"Unknown memory provider '{binding.provider}'.")
            memory_blocks.extend(
                provider.build(session=session, transcript=transcript, binding=binding, paths=paths)
            )

        return ContextSnapshot(blocks=context_blocks, memory=memory_blocks)
