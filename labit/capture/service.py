from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from labit.capture.models import CaptureRecord, IdeaDraft
from labit.chat.models import ChatSession
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


class CaptureService:
    def __init__(self, paths: RepoPaths, *, project_service: ProjectService | None = None):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)

    def list_ideas(self, project: str) -> list[CaptureRecord]:
        return self._list_records(project, kind="idea")

    def list_todos(self, project: str) -> list[CaptureRecord]:
        return self._list_records(project, kind="todo")

    def save_idea(self, *, project: str, draft: IdeaDraft, session: ChatSession, intent: str = "") -> CaptureRecord:
        return self._save_idea_record(project=project, draft=draft, session=session, intent=intent)

    def save_todo(self, *, project: str, content: str, session: ChatSession) -> CaptureRecord:
        return self._save_record(project=project, kind="todo", content=content, session=session)

    def _save_idea_record(
        self,
        *,
        project: str,
        draft: IdeaDraft,
        session: ChatSession,
        intent: str = "",
    ) -> CaptureRecord:
        resolved = self._require_project(project)
        now = datetime.now(UTC)
        date_text = now.date().isoformat()
        target_dir = self._target_dir(resolved, kind="idea")
        target_dir.mkdir(parents=True, exist_ok=True)
        path = self._next_path(target_dir, slug=self._slugify(draft.title), date_text=date_text)
        source = self._source_label(session)
        body = self._render_idea_body(
            title=draft.title,
            date_text=date_text,
            source=source,
            summary_markdown=draft.summary_markdown,
            key_question=draft.key_question,
            intent=intent.strip(),
        )
        self._atomic_write(path, body)
        return CaptureRecord(
            kind="idea",
            title=draft.title,
            path=str(path.relative_to(self.paths.root)),
            source=source,
            created_at=now.replace(microsecond=0).isoformat(),
        )

    def _save_record(self, *, project: str, kind: str, content: str, session: ChatSession) -> CaptureRecord:
        resolved = self._require_project(project)
        text = content.strip()
        if not text:
            raise ValueError(f"{kind.title()} content cannot be empty.")

        now = datetime.now(UTC)
        date_text = now.date().isoformat()
        title = self._derive_title(text)
        target_dir = self._target_dir(resolved, kind=kind)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = self._next_path(target_dir, slug=self._slugify(title), date_text=date_text)
        source = self._source_label(session)
        body = self._render_body(kind=kind, title=title, date_text=date_text, source=source, content=text)
        self._atomic_write(path, body)
        return CaptureRecord(
            kind=kind,
            title=title,
            path=str(path.relative_to(self.paths.root)),
            source=source,
            created_at=now.replace(microsecond=0).isoformat(),
        )

    def _list_records(self, project: str, *, kind: str) -> list[CaptureRecord]:
        resolved = self._require_project(project)
        target_dir = self._target_dir(resolved, kind=kind)
        if not target_dir.exists():
            return []
        records: list[CaptureRecord] = []
        for path in sorted(target_dir.glob("*.md"), reverse=True):
            text = path.read_text(encoding="utf-8")
            title = self._read_heading(text) or path.stem
            source = self._read_field(text, "Source") or "(unknown)"
            created_at = self._read_field(text, "Date") or ""
            records.append(
                CaptureRecord(
                    kind=kind,
                    title=title,
                    path=str(path.relative_to(self.paths.root)),
                    source=source,
                    created_at=created_at,
                )
            )
        return records

    def _require_project(self, project: str) -> str:
        resolved = self.project_service.resolve_project_name(project)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{project}' not found. Available projects: {', '.join(self.project_service.list_project_names()) or '(none)'}"
            )
        return resolved

    def _target_dir(self, project: str, *, kind: str) -> Path:
        folder_map = {
            "idea": "ideas",
            "todo": "todos",
        }
        folder = folder_map.get(kind, f"{kind}s")
        return self.paths.vault_projects_dir / project / "docs" / folder

    def _next_path(self, target_dir: Path, *, slug: str, date_text: str) -> Path:
        base = f"{slug}-{date_text}"
        candidate = target_dir / f"{base}.md"
        index = 2
        while candidate.exists():
            candidate = target_dir / f"{base}-{index}.md"
            index += 1
        return candidate

    def _derive_title(self, text: str) -> str:
        stripped = " ".join(text.split())
        if not stripped:
            return "Untitled"
        first_sentence = re.split(r"[.!?]\s+", stripped, maxsplit=1)[0]
        words = first_sentence.split()
        if len(words) > 10:
            first_sentence = " ".join(words[:10])
        return first_sentence[:120].strip()

    def _slugify(self, text: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
        return slug or "untitled"

    def _source_label(self, session: ChatSession) -> str:
        return f"chat:{session.title} · {session.session_id}"

    def _render_idea_body(
        self,
        *,
        title: str,
        date_text: str,
        source: str,
        summary_markdown: str,
        key_question: str,
        intent: str,
    ) -> str:
        lines = [
            f"# Idea: {title}",
            "",
            f"**Date**: {date_text}",
            f"**Source**: {source}",
            "**Status**: raw",
        ]
        if intent:
            lines.append(f"**Intent**: {intent}")
        lines.extend(["", summary_markdown.strip(), "", f"**Key question**: {key_question.strip()}", ""])
        return "\n".join(lines)

    def _render_body(self, *, kind: str, title: str, date_text: str, source: str, content: str) -> str:
        heading_map = {
            "idea": "Idea",
            "todo": "Todo",
        }
        heading = heading_map.get(kind, kind.title())
        lines = [
            f"# {heading}: {title}",
            "",
            f"**Date**: {date_text}",
            f"**Source**: {source}",
        ]
        if kind == "idea":
            lines.append("**Status**: raw")
        elif kind == "todo":
            lines.append("**Status**: open")
        lines.extend(["", content.strip()])
        lines.append("")
        return "\n".join(lines)

    def _read_heading(self, text: str) -> str:
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return ""

    def _read_field(self, text: str, label: str) -> str:
        prefix = f"**{label}**:"
        for line in text.splitlines():
            if line.startswith(prefix):
                return line.split(":", 1)[1].strip()
        return ""

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
