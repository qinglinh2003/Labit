from __future__ import annotations
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml

from labit.papers.models import (
    DuplicateMatch,
    DuplicateStatus,
    GlobalPaperIndex,
    GlobalPaperIndexEntry,
    GlobalPaperMeta,
    GlobalPaperRecord,
    PaperContentFormat,
    PaperLibraryOverview,
    ProjectPaperIndex,
    ProjectPaperIndexEntry,
    ProjectPaperRecord,
    ProjectPaperStatus,
    normalize_title,
    utc_now_iso,
)
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


class PaperService:
    def __init__(self, paths: RepoPaths):
        self.paths = paths
        self.project_service = ProjectService(paths)

    def ensure_global_library(self) -> None:
        self.paths.papers_dir.mkdir(parents=True, exist_ok=True)
        self.paths.papers_by_id_dir.mkdir(parents=True, exist_ok=True)
        if not self.paths.papers_index_path.exists():
            self.save_global_index(GlobalPaperIndex())

    def ensure_project_library(self, project: str) -> Path:
        directory = self.project_key_papers_dir(project)
        directory.mkdir(parents=True, exist_ok=True)
        index_path = self.project_index_path(project)
        if not index_path.exists():
            self.save_project_index(project, ProjectPaperIndex(project=project))
        return directory

    def global_paper_dir(self, paper_id: str) -> Path:
        return self.paths.papers_by_id_dir / paper_id

    def project_key_papers_dir(self, project: str) -> Path:
        return self.paths.vault_projects_dir / project / "key_papers"

    def project_key_paper_dir(self, project: str, paper_id: str) -> Path:
        return self.project_key_papers_dir(project) / paper_id

    def project_index_path(self, project: str) -> Path:
        return self.project_key_papers_dir(project) / "index.yaml"

    def project_paper_path(self, project: str, paper_id: str) -> Path:
        return self.project_key_paper_dir(project, paper_id) / "paper.yaml"

    def legacy_project_link_path(self, project: str, paper_id: str) -> Path:
        return self.project_key_paper_dir(project, paper_id) / "link.yaml"

    def project_summary_path(self, project: str, paper_id: str) -> Path:
        return self.project_key_paper_dir(project, paper_id) / "summary.md"

    def project_notes_path(self, project: str, paper_id: str) -> Path:
        return self.project_key_paper_dir(project, paper_id) / "notes.md"

    def global_relative_dir(self, paper_id: str) -> str:
        return str(Path("vault") / "papers" / "by_id" / paper_id)

    def relative_path(self, path: Path) -> str:
        return str(path.relative_to(self.paths.root))

    def load_global_index(self) -> GlobalPaperIndex:
        if not self.paths.papers_index_path.exists():
            return GlobalPaperIndex()
        raw = yaml.safe_load(self.paths.papers_index_path.read_text()) or {}
        if isinstance(raw, list):
            raw = {"papers": raw}
        return GlobalPaperIndex.model_validate(raw, extra="ignore")

    def save_global_index(self, index: GlobalPaperIndex) -> None:
        payload = index.model_dump(mode="json", exclude_none=True)
        self._atomic_yaml(self.paths.papers_index_path, payload)

    def load_project_index(self, project: str) -> ProjectPaperIndex:
        index_path = self.project_index_path(project)
        if not index_path.exists():
            return ProjectPaperIndex(project=project)
        raw = yaml.safe_load(index_path.read_text()) or {}
        if isinstance(raw, list):
            raw = {"project": project, "papers": raw}
        raw.setdefault("project", project)
        return ProjectPaperIndex.model_validate(raw, extra="ignore")

    def save_project_index(self, project: str, index: ProjectPaperIndex) -> None:
        payload = index.model_dump(mode="json", exclude_none=True)
        self._atomic_yaml(self.project_index_path(project), payload)

    def list_global_index_entries(self) -> list[GlobalPaperIndexEntry]:
        return self.load_global_index().papers

    def list_project_index_entries(self, project: str) -> list[ProjectPaperIndexEntry]:
        return self.load_project_index(project).papers

    def load_global_record(self, paper_id: str) -> GlobalPaperRecord:
        global_dir = self.global_paper_dir(paper_id)
        meta_path = global_dir / "meta.yaml"
        if not meta_path.exists():
            raise FileNotFoundError(f"Paper '{paper_id}' not found in the global library.")

        raw = yaml.safe_load(meta_path.read_text()) or {}
        meta = GlobalPaperMeta.model_validate(raw)
        linked_projects = self._linked_projects_for_paper(meta.paper_id)
        html_path = global_dir / "paper.html"
        pdf_path = global_dir / "paper.pdf"
        return GlobalPaperRecord(
            meta=meta,
            global_dir=str(global_dir),
            html_path=str(html_path) if html_path.exists() else None,
            pdf_path=str(pdf_path) if pdf_path.exists() else None,
            linked_projects=linked_projects,
        )

    def load_project_record(self, project: str, paper_id: str) -> ProjectPaperRecord:
        paper_path = self.project_paper_path(project, paper_id)
        legacy_path = self.legacy_project_link_path(project, paper_id)

        if paper_path.exists():
            raw = yaml.safe_load(paper_path.read_text()) or {}
            return ProjectPaperRecord.model_validate(raw, extra="ignore")

        if legacy_path.exists():
            raw = yaml.safe_load(legacy_path.read_text()) or {}
            global_record = self.load_global_record(paper_id)
            summary_path = self.project_summary_path(project, paper_id)
            notes_path = self.project_notes_path(project, paper_id)
            return ProjectPaperRecord(
                paper_id=paper_id,
                project=project,
                title=raw.get("title") or global_record.meta.title,
                global_dir=self.global_relative_dir(paper_id),
                meta_path=self.relative_path(self.global_paper_dir(paper_id) / "meta.yaml"),
                html_path=self.relative_path(self.global_paper_dir(paper_id) / "paper.html")
                if (self.global_paper_dir(paper_id) / "paper.html").exists()
                else None,
                pdf_path=self.relative_path(self.global_paper_dir(paper_id) / "paper.pdf")
                if (self.global_paper_dir(paper_id) / "paper.pdf").exists()
                else None,
                status=raw.get("status", ProjectPaperStatus.PULLED),
                summary_path=self.relative_path(summary_path) if summary_path.exists() else None,
                notes_path=self.relative_path(notes_path) if notes_path.exists() else None,
                added_at=raw.get("added_at") or raw.get("added") or utc_now_iso(),
                updated_at=raw.get("updated_at") or utc_now_iso(),
            )

        raise FileNotFoundError(f"Paper '{paper_id}' is not linked in project '{project}'.")

    def build_overview(self, project: str | None) -> PaperLibraryOverview:
        project_entries = self.list_project_index_entries(project) if project else []
        return PaperLibraryOverview(
            active_project=project,
            global_paper_count=len(self.list_global_index_entries()),
            project_paper_count=len(project_entries),
            project_papers=project_entries[:10],
        )

    def find_duplicate(self, meta: GlobalPaperMeta, *, project: str | None = None) -> DuplicateMatch:
        global_index = self.load_global_index()
        normalized = normalize_title(meta.title)

        for entry in global_index.papers:
            if entry.paper_id == meta.paper_id:
                return self._build_duplicate(entry, meta, project=project, reason="paper_id")

        incoming_external_ids = set(meta.external_ids.values())
        if incoming_external_ids:
            for entry in global_index.papers:
                if incoming_external_ids.intersection(entry.external_ids.values()):
                    return self._build_duplicate(entry, meta, project=project, reason="external_id")

        for entry in global_index.papers:
            if entry.normalized_title == normalized:
                return self._build_duplicate(entry, meta, project=project, reason="title")

        return DuplicateMatch(status=DuplicateStatus.NEW, metadata={"normalized_title": normalized})

    def pull_paper(
        self,
        *,
        project: str,
        meta: GlobalPaperMeta,
        html_content: str | None = None,
        pdf_bytes: bytes | None = None,
    ) -> dict:
        self.project_service.load_project(project)
        meta = meta.model_copy(update={"relevance_to": sorted(set(meta.relevance_to + [project]))})
        canonical = self._upsert_global_paper(meta, html_content=html_content, pdf_bytes=pdf_bytes)
        project_record = self._materialize_project_paper(
            project,
            canonical,
            status=ProjectPaperStatus.PULLED,
        )
        return {
            "paper_id": canonical.meta.paper_id,
            "project": project,
            "global_dir": canonical.global_dir,
            "project_dir": str(self.project_key_paper_dir(project, canonical.meta.paper_id)),
            "status": project_record.status.value,
        }

    def ingest_paper(
        self,
        *,
        project: str,
        meta: GlobalPaperMeta,
        summary_markdown: str,
        html_content: str | None = None,
        pdf_bytes: bytes | None = None,
    ) -> dict:
        if not summary_markdown.strip():
            raise ValueError("Summary markdown cannot be empty for ingest.")
        self.project_service.load_project(project)
        meta = meta.model_copy(update={"relevance_to": sorted(set(meta.relevance_to + [project]))})
        canonical = self._upsert_global_paper(
            meta,
            html_content=html_content,
            pdf_bytes=pdf_bytes,
        )
        project_record = self._materialize_project_paper(
            project,
            canonical,
            status=ProjectPaperStatus.INGESTED,
            summary_markdown=summary_markdown,
        )
        return {
            "paper_id": canonical.meta.paper_id,
            "project": project,
            "global_dir": canonical.global_dir,
            "project_dir": str(self.project_key_paper_dir(project, canonical.meta.paper_id)),
            "status": project_record.status.value,
            "summary_path": project_record.summary_path,
        }

    def _upsert_global_paper(
        self,
        meta: GlobalPaperMeta,
        *,
        html_content: str | None = None,
        pdf_bytes: bytes | None = None,
    ) -> GlobalPaperRecord:
        self.ensure_global_library()
        duplicate = self.find_duplicate(meta)
        paper_id = duplicate.paper_id or meta.paper_id
        global_dir = self.global_paper_dir(paper_id)
        global_dir.mkdir(parents=True, exist_ok=True)

        meta_path = global_dir / "meta.yaml"
        if meta_path.exists():
            existing = GlobalPaperMeta.model_validate(yaml.safe_load(meta_path.read_text()) or {})
            meta = self._merge_meta(existing, meta)
        else:
            meta = meta.model_copy(update={"paper_id": paper_id, "updated_at": utc_now_iso()})

        if html_content is not None:
            (global_dir / "paper.html").write_text(html_content, encoding="utf-8")
            meta = meta.model_copy(update={"content_format": PaperContentFormat.HTML, "updated_at": utc_now_iso()})

        if pdf_bytes is not None:
            (global_dir / "paper.pdf").write_bytes(pdf_bytes)
            if meta.content_format is None:
                meta = meta.model_copy(update={"content_format": PaperContentFormat.PDF, "updated_at": utc_now_iso()})

        self._atomic_yaml(meta_path, meta.model_dump(mode="json", exclude_none=True))
        self._update_global_index(meta)

        return self.load_global_record(meta.paper_id)

    def _materialize_project_paper(
        self,
        project: str,
        global_record: GlobalPaperRecord,
        *,
        status: ProjectPaperStatus,
        summary_markdown: str | None = None,
    ) -> ProjectPaperRecord:
        self.ensure_project_library(project)
        paper_id = global_record.meta.paper_id
        global_dir = self.global_paper_dir(paper_id)
        if not global_dir.exists():
            raise FileNotFoundError(f"Global paper directory does not exist for '{paper_id}'.")

        paper_dir = self.project_key_paper_dir(project, paper_id)
        paper_dir.mkdir(parents=True, exist_ok=True)

        existing_paper_path = self.project_paper_path(project, paper_id)
        if existing_paper_path.exists():
            existing = ProjectPaperRecord.model_validate(yaml.safe_load(existing_paper_path.read_text()) or {})
            status = status if status == ProjectPaperStatus.INGESTED else existing.status
            added_at = existing.added_at
        else:
            added_at = utc_now_iso()

        summary_path = self.project_summary_path(project, paper_id)
        if summary_markdown is not None:
            summary_path.write_text(summary_markdown.strip() + "\n", encoding="utf-8")

        record = ProjectPaperRecord(
            paper_id=paper_id,
            project=project,
            title=global_record.meta.title,
            global_dir=self.global_relative_dir(paper_id),
            meta_path=self.relative_path(global_dir / "meta.yaml"),
            html_path=self.relative_path(global_dir / "paper.html") if (global_dir / "paper.html").exists() else None,
            pdf_path=self.relative_path(global_dir / "paper.pdf") if (global_dir / "paper.pdf").exists() else None,
            status=status,
            summary_path=self.relative_path(summary_path) if summary_path.exists() else None,
            notes_path=self.relative_path(self.project_notes_path(project, paper_id))
            if self.project_notes_path(project, paper_id).exists()
            else None,
            added_at=added_at,
            updated_at=utc_now_iso(),
        )
        self._atomic_yaml(existing_paper_path, record.model_dump(mode="json", exclude_none=True))
        legacy_link_path = self.legacy_project_link_path(project, paper_id)
        if legacy_link_path.exists():
            legacy_link_path.unlink()
        self._update_project_index(project, record)
        self._update_global_index(global_record.meta)
        return record

    def _linked_projects_for_paper(self, paper_id: str) -> list[str]:
        linked: list[str] = []
        for project in self.project_service.list_project_names():
            if self.project_paper_path(project, paper_id).exists() or self.legacy_project_link_path(project, paper_id).exists():
                linked.append(project)
        return linked

    def _update_global_index(self, meta: GlobalPaperMeta) -> None:
        index = self.load_global_index()
        linked_projects = self._linked_projects_for_paper(meta.paper_id)
        entry = GlobalPaperIndexEntry(
            paper_id=meta.paper_id,
            title=meta.title,
            normalized_title=normalize_title(meta.title),
            title_aliases=[],
            year=meta.year,
            external_ids=meta.external_ids,
            path=self.global_relative_dir(meta.paper_id),
            linked_projects=linked_projects,
        )

        updated: list[GlobalPaperIndexEntry] = []
        replaced = False
        for existing in index.papers:
            if existing.paper_id == meta.paper_id:
                updated.append(entry)
                replaced = True
            else:
                updated.append(existing)
        if not replaced:
            updated.append(entry)

        index = GlobalPaperIndex(papers=sorted(updated, key=lambda item: item.paper_id))
        self.save_global_index(index)

    def _update_project_index(self, project: str, record: ProjectPaperRecord) -> None:
        index = self.load_project_index(project)
        entry = ProjectPaperIndexEntry(
            paper_id=record.paper_id,
            title=record.title,
            path=str(Path("vault") / "projects" / project / "key_papers" / record.paper_id),
            status=record.status,
            added_at=record.added_at,
        )

        updated: list[ProjectPaperIndexEntry] = []
        replaced = False
        for existing in index.papers:
            if existing.paper_id == record.paper_id:
                updated.append(entry)
                replaced = True
            else:
                updated.append(existing)
        if not replaced:
            updated.append(entry)

        self.save_project_index(project, ProjectPaperIndex(project=project, papers=sorted(updated, key=lambda item: item.paper_id)))

    def _build_duplicate(
        self,
        entry: GlobalPaperIndexEntry,
        meta: GlobalPaperMeta,
        *,
        project: str | None,
        reason: str,
    ) -> DuplicateMatch:
        linked_projects = set(entry.linked_projects)
        if project and project in linked_projects:
            status = DuplicateStatus.IN_GLOBAL_AND_PROJECT
        elif project:
            status = DuplicateStatus.IN_GLOBAL
        else:
            status = DuplicateStatus.IN_GLOBAL

        if project and not entry.linked_projects:
            project_link = self.project_paper_path(project, entry.paper_id).exists() or self.legacy_project_link_path(project, entry.paper_id).exists()
            if project_link:
                status = DuplicateStatus.IN_GLOBAL_AND_PROJECT

        return DuplicateMatch(
            status=status,
            paper_id=entry.paper_id,
            reason=reason,
            project=project,
            metadata={
                "matched_title": entry.title,
                "incoming_title": meta.title,
                "linked_projects": entry.linked_projects,
            },
        )

    def _merge_meta(self, existing: GlobalPaperMeta, incoming: GlobalPaperMeta) -> GlobalPaperMeta:
        merged = existing.model_dump(mode="json")
        incoming_payload = incoming.model_dump(mode="json", exclude_none=True)

        for key, value in incoming_payload.items():
            if key == "relevance_to":
                merged[key] = sorted(set(existing.relevance_to).union(value))
                continue
            if key == "authors":
                merged[key] = existing.authors or value
                continue
            if key == "external_ids":
                current_ids = existing.external_ids.model_dump(mode="json", exclude_none=True)
                current_ids.update({k: v for k, v in value.items() if v})
                merged[key] = current_ids
                continue
            if value not in (None, "", [], {}):
                merged[key] = value

        merged["updated_at"] = utc_now_iso()
        return GlobalPaperMeta.model_validate(merged)

    def _atomic_yaml(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        temp_path.replace(path)
