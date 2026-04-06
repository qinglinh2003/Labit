from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

from labit.chat.models import ChatSession
from labit.documents.models import DocSession, DocStatus, DocUpdate
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


class DocumentService:
    def __init__(self, paths: RepoPaths, *, project_service: ProjectService | None = None):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)

    # ── Create ──────────────────────────────────────────────

    def start_document(self, *, project: str, title: str, update: DocUpdate, session: ChatSession) -> DocSession:
        resolved = self._require_project(project)
        title = title.strip() or update.title
        if not title.strip():
            raise ValueError("Document title cannot be empty.")

        now = datetime.now(UTC).replace(microsecond=0)
        date_text = now.date().isoformat()
        target_dir = self._designs_dir(resolved)
        target_dir.mkdir(parents=True, exist_ok=True)

        doc_id = self._next_doc_id(resolved)
        path = self._next_path(target_dir, slug=self._slugify(title), date_text=date_text)
        source = self._source_label(session)

        markdown = self._inject_frontmatter(
            update.markdown,
            doc_id=doc_id,
            title=update.title or title,
            status=DocStatus.DRAFT,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            source_session_id=session.session_id,
        )
        self._atomic_write(path, markdown)

        log_path = self._sessions_dir(resolved) / f"{path.stem}.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        doc_session = DocSession(
            project=resolved,
            doc_id=doc_id,
            title=update.title or title,
            status=DocStatus.DRAFT,
            document_path=str(path.relative_to(self.paths.root)),
            log_path=str(log_path.relative_to(self.paths.root)),
            source=source,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            iteration=0,
        )
        self._append_log(
            log_path,
            {
                "type": "session_started",
                "iteration": 0,
                "timestamp": now.isoformat(),
                "doc_id": doc_id,
                "title": doc_session.title,
                "summary": update.summary,
                "document_path": doc_session.document_path,
                "markdown_sha256": self._sha256(markdown),
                "source": source,
            },
        )
        return doc_session

    # ── Open existing ───────────────────────────────────────

    def open_document(self, *, project: str, doc_id: str, session: ChatSession) -> DocSession:
        """Re-open an existing document for editing. Demotes active → draft."""
        resolved = self._require_project(project)
        path, meta = self._find_document(resolved, doc_id)
        if path is None or meta is None:
            raise FileNotFoundError(f"Document '{doc_id}' not found in project '{resolved}'.")

        now = datetime.now(UTC).replace(microsecond=0)
        source = self._source_label(session)

        # Demote active → draft (editing invalidates the "active" promise)
        old_status = DocStatus(meta.get("status", "draft"))
        new_status = DocStatus.DRAFT if old_status == DocStatus.ACTIVE else old_status
        if new_status != old_status:
            content = path.read_text(encoding="utf-8")
            content = self._update_frontmatter_field(content, "status", new_status.value)
            self._atomic_write(path, content)

        log_path = self._sessions_dir(resolved) / f"{path.stem}.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        doc_session = DocSession(
            project=resolved,
            doc_id=doc_id,
            title=meta.get("title", path.stem),
            status=new_status,
            document_path=str(path.relative_to(self.paths.root)),
            log_path=str(log_path.relative_to(self.paths.root)),
            source=source,
            created_at=meta.get("created_at", ""),
            updated_at=meta.get("updated_at", ""),
            iteration=0,
        )
        self._append_log(
            log_path,
            {
                "type": "session_started",
                "timestamp": now.isoformat(),
                "doc_id": doc_id,
                "source": source,
                "previous_status": old_status.value,
                "new_status": new_status.value,
            },
        )
        return doc_session

    # ── Revise ──────────────────────────────────────────────

    def revise_document(self, *, doc_session: DocSession, update: DocUpdate, user_instruction: str) -> DocSession:
        path = self.paths.root / doc_session.document_path
        log_path = self.paths.root / doc_session.log_path
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {doc_session.document_path}")

        now = datetime.now(UTC).replace(microsecond=0)
        previous_content = path.read_text(encoding="utf-8")
        previous_hash = self._sha256(previous_content)

        markdown = self._inject_frontmatter(
            update.markdown,
            doc_id=doc_session.doc_id,
            title=update.title or doc_session.title,
            status=doc_session.status,
            created_at=doc_session.created_at,
            updated_at=now.isoformat(),
            source_session_id=None,  # preserve existing
        )
        final_hash = self._sha256(markdown)

        # Only update updated_at if content actually changed
        if final_hash == previous_hash:
            markdown = self._update_frontmatter_field(
                markdown, "updated_at", doc_session.updated_at
            )
            final_hash = self._sha256(markdown)

        self._atomic_write(path, markdown)
        next_iteration = doc_session.iteration + 1
        self._append_log(
            log_path,
            {
                "type": "agent_revision",
                "iteration": next_iteration,
                "timestamp": now.isoformat(),
                "user_instruction": user_instruction.strip(),
                "agent_summary": update.summary,
                "document_path": doc_session.document_path,
                "previous_sha256": previous_hash,
                "markdown_sha256": final_hash,
            },
        )
        actual_updated_at = now.isoformat() if final_hash != previous_hash else doc_session.updated_at
        return DocSession(
            project=doc_session.project,
            doc_id=doc_session.doc_id,
            title=update.title or doc_session.title,
            status=doc_session.status,
            document_path=doc_session.document_path,
            log_path=doc_session.log_path,
            source=doc_session.source,
            created_at=doc_session.created_at,
            updated_at=actual_updated_at,
            iteration=next_iteration,
        )

    # ── End session ─────────────────────────────────────────

    def end_session(self, doc_session: DocSession) -> None:
        """Log session_ended to JSONL. Does NOT change document status."""
        log_path = self.paths.root / doc_session.log_path
        now = datetime.now(UTC).replace(microsecond=0)
        self._append_log(
            log_path,
            {
                "type": "session_ended",
                "timestamp": now.isoformat(),
                "doc_id": doc_session.doc_id,
                "iterations": doc_session.iteration,
            },
        )

    # ── Publish ─────────────────────────────────────────────

    def publish_document(
        self,
        *,
        project: str,
        doc_id: str,
        source_session: ChatSession | None = None,
    ) -> DocSession:
        """Promote a document to active by doc_id."""
        resolved = self._require_project(project)
        path, meta = self._find_document(resolved, doc_id)
        if path is None or meta is None:
            raise FileNotFoundError(f"Document '{doc_id}' not found in project '{resolved}'.")

        status = DocStatus(meta.get("status", DocStatus.DRAFT.value))
        source = self._source_label(source_session) if source_session is not None else "labit"
        log_path = self._sessions_dir(resolved) / f"{path.stem}.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        if status == DocStatus.ACTIVE:
            return DocSession(
                project=resolved,
                doc_id=doc_id,
                title=meta.get("title", path.stem),
                status=DocStatus.ACTIVE,
                document_path=str(path.relative_to(self.paths.root)),
                log_path=str(log_path.relative_to(self.paths.root)),
                source=source,
                created_at=meta.get("created_at", ""),
                updated_at=meta.get("updated_at", ""),
                iteration=0,
            )

        content = path.read_text(encoding="utf-8")
        content = self._update_frontmatter_field(content, "status", DocStatus.ACTIVE.value)
        self._atomic_write(path, content)
        now = datetime.now(UTC).replace(microsecond=0)
        self._append_log(
            log_path,
            {
                "type": "status_changed",
                "timestamp": now.isoformat(),
                "doc_id": doc_id,
                "old_status": status.value,
                "new_status": DocStatus.ACTIVE.value,
                "source": source,
            },
        )
        return DocSession(
            project=resolved,
            doc_id=doc_id,
            title=meta.get("title", path.stem),
            status=DocStatus.ACTIVE,
            document_path=str(path.relative_to(self.paths.root)),
            log_path=str(log_path.relative_to(self.paths.root)),
            source=source,
            created_at=meta.get("created_at", ""),
            updated_at=meta.get("updated_at", ""),
            iteration=0,
        )

    # ── Read ────────────────────────────────────────────────

    def read_document(self, doc_session: DocSession) -> str:
        return (self.paths.root / doc_session.document_path).read_text(encoding="utf-8")

    def interaction_excerpt(self, doc_session: DocSession, *, limit: int = 12, max_chars: int = 6000) -> str:
        log_path = self.paths.root / doc_session.log_path
        if not log_path.exists():
            return ""
        lines = log_path.read_text(encoding="utf-8").splitlines()[-limit:]
        rendered: list[str] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry_type = item.get("type", "")
            if entry_type == "session_started":
                rendered.append(f"[session started] {item.get('summary', '')}")
            elif entry_type == "agent_revision":
                rendered.append(
                    f"[{item.get('iteration')}] user: {item.get('user_instruction', '')}\n"
                    f"summary: {item.get('agent_summary', '')}"
                )
            elif entry_type == "session_ended":
                rendered.append("[session ended]")
        text = "\n\n".join(rendered).strip()
        return text[:max_chars].strip()

    # ── List documents ──────────────────────────────────────

    def list_documents(self, project: str) -> list[dict[str, Any]]:
        """Return metadata for all documents in a project."""
        resolved = self._require_project(project)
        designs_dir = self._designs_dir(resolved)
        if not designs_dir.exists():
            return []
        docs = []
        for md_file in sorted(designs_dir.glob("*.md")):
            meta = self._read_frontmatter(md_file)
            if meta:
                meta["file"] = str(md_file.relative_to(self.paths.root))
                docs.append(meta)
        return docs

    # ── Internal ────────────────────────────────────────────

    def _require_project(self, project: str) -> str:
        resolved = self.project_service.resolve_project_name(project)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{project}' not found. Available projects: {', '.join(self.project_service.list_project_names()) or '(none)'}"
            )
        return resolved

    def _designs_dir(self, project: str) -> Path:
        return self.paths.vault_projects_dir / project / "docs" / "designs"

    def _sessions_dir(self, project: str) -> Path:
        return self._designs_dir(project) / ".sessions"

    def _next_path(self, target_dir: Path, *, slug: str, date_text: str) -> Path:
        base = f"{date_text}_{slug}"
        candidate = target_dir / f"{base}.md"
        index = 2
        while candidate.exists():
            candidate = target_dir / f"{base}-{index}.md"
            index += 1
        return candidate

    def _next_doc_id(self, project: str) -> str:
        """Scan existing documents and return next available doc_id (d01, d02, ...)."""
        designs_dir = self._designs_dir(project)
        if not designs_dir.exists():
            return "d01"
        max_num = 0
        for md_file in designs_dir.glob("*.md"):
            meta = self._read_frontmatter(md_file)
            if meta and "doc_id" in meta:
                try:
                    num = int(meta["doc_id"].lstrip("d"))
                    max_num = max(max_num, num)
                except (ValueError, AttributeError):
                    pass
        return f"d{max_num + 1:02d}"

    def _find_document(self, project: str, doc_id: str) -> tuple[Path | None, dict[str, Any] | None]:
        """Find a document by doc_id in a project."""
        designs_dir = self._designs_dir(project)
        if not designs_dir.exists():
            return None, None
        for md_file in designs_dir.glob("*.md"):
            meta = self._read_frontmatter(md_file)
            if meta and meta.get("doc_id") == doc_id:
                return md_file, meta
        return None, None

    def _read_frontmatter(self, path: Path) -> dict[str, Any] | None:
        """Extract YAML frontmatter from a markdown file."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        if not text.startswith("---"):
            return None
        end = text.find("---", 3)
        if end < 0:
            return None
        try:
            return yaml.safe_load(text[3:end])
        except yaml.YAMLError:
            return None

    def _inject_frontmatter(
        self,
        markdown: str,
        *,
        doc_id: str,
        title: str,
        status: DocStatus,
        created_at: str,
        updated_at: str,
        source_session_id: str | None,
    ) -> str:
        """Strip any existing frontmatter and prepend a fresh one."""
        body = self._strip_frontmatter(markdown)
        fm: dict[str, Any] = {
            "doc_id": doc_id,
            "title": title,
            "status": status.value,
            "created_at": created_at,
            "updated_at": updated_at,
        }
        if source_session_id:
            fm["source_session_id"] = source_session_id
        fm_text = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
        return f"---\n{fm_text}\n---\n\n{body}"

    def _strip_frontmatter(self, markdown: str) -> str:
        """Remove YAML frontmatter if present, return the body."""
        text = markdown.strip()
        if not text.startswith("---"):
            return self._normalize_markdown(text)
        end = text.find("---", 3)
        if end < 0:
            return self._normalize_markdown(text)
        return self._normalize_markdown(text[end + 3:])

    def _update_frontmatter_field(self, content: str, field: str, value: str) -> str:
        """Update a single field in the YAML frontmatter."""
        if not content.startswith("---"):
            return content
        end = content.find("---", 3)
        if end < 0:
            return content
        fm_text = content[3:end]
        try:
            fm = yaml.safe_load(fm_text)
        except yaml.YAMLError:
            return content
        if not isinstance(fm, dict):
            return content
        fm[field] = value
        new_fm = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
        body = content[end + 3:]
        return f"---\n{new_fm}\n---{body}"

    def _slugify(self, text: str) -> str:
        slug = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
        if not slug:
            slug = "doc-" + hashlib.sha1(text.encode()).hexdigest()[:8]
        return slug

    def _source_label(self, session: ChatSession) -> str:
        paper_id = ""
        for binding in session.context_bindings:
            if binding.provider == "paper_focus":
                paper_id = str(binding.config.get("paper_id", "")).strip()
                break
        if paper_id:
            return f"paper_focus:{paper_id} · {session.title} · {session.session_id}"
        return f"chat:{session.title} · {session.session_id}"

    def _normalize_markdown(self, markdown: str) -> str:
        return markdown.strip() + "\n"

    def _sha256(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _append_log(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
