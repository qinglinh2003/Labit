from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep

import yaml

from labit.papers.models import ArxivPaperMetadata, PaperRecord
from labit.papers.render import generate_page1_cache, is_manifest_current, load_manifest
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_ABS_URL = "https://arxiv.org/abs/{arxiv_id}"
ARXIV_HTML_URL = "https://arxiv.org/html/{arxiv_id}"
AR5IV_HTML_URL = "https://ar5iv.labs.arxiv.org/html/{arxiv_id}"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
S2_API_URL = "https://api.semanticscholar.org/graph/v1/paper/ARXIV:{arxiv_id}"
S2_FIELDS = "title,authors,abstract,externalIds,url,publicationDate"
ARXIV_ID_RE = re.compile(r"(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)")


class PaperService:
    def __init__(self, paths: RepoPaths, *, project_service: ProjectService | None = None):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)

    def add_paper(self, *, project: str, reference: str) -> PaperRecord:
        resolved = self._require_project(project)
        arxiv_id = self.parse_arxiv_id(reference)

        # Cache-first: if we already have metadata locally, return it
        metadata_path = self._metadata_path(resolved, arxiv_id)
        if metadata_path.exists():
            raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
            return PaperRecord.model_validate(raw)

        metadata = self._fetch_metadata(arxiv_id)

        sleep(1)  # brief pause before HTML fetch

        html = ""
        html_url = ARXIV_HTML_URL.format(arxiv_id=arxiv_id)
        html_fetch_error = ""
        try:
            html, html_url = self._fetch_html(arxiv_id)
        except Exception as exc:
            html_fetch_error = str(exc)

        paper_dir = self._paper_record_dir(resolved, arxiv_id)
        paper_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = paper_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = paper_dir / "paper.yaml"
        html_path = paper_dir / "paper.html"
        notes_path = artifacts_dir / "notes.md"
        if not notes_path.exists():
            self._atomic_write(notes_path, f"# {metadata['title']}\n")

        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        record = PaperRecord(
            id=f"arxiv:{arxiv_id}",
            source="arxiv",
            arxiv_id=arxiv_id,
            title=metadata["title"],
            authors=metadata["authors"],
            abstract=metadata["abstract"],
            url=ARXIV_ABS_URL.format(arxiv_id=arxiv_id),
            source_url=ARXIV_ABS_URL.format(arxiv_id=arxiv_id),
            html_url=html_url,
            html_fetch_error=html_fetch_error,
            pdf_url=ARXIV_PDF_URL.format(arxiv_id=arxiv_id),
            local_html_path=str(html_path.relative_to(self.paths.root)) if html else "",
            local_metadata_path=str(metadata_path.relative_to(self.paths.root)),
            artifact_dir_path=str(artifacts_dir.relative_to(self.paths.root)),
            submitted_date=metadata.get("submitted_date", ""),
            added_at=now,
        )

        if html:
            self._atomic_write(html_path, html)
        yaml_text = yaml.safe_dump(record.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        self._atomic_write(metadata_path, yaml_text)
        return record

    def import_arxiv_pdf(
        self,
        *,
        project: str,
        metadata: ArxivPaperMetadata,
        pdf_content: bytes,
    ) -> PaperRecord:
        resolved = self._require_project(project)
        arxiv_id = self.parse_arxiv_id(metadata.arxiv_id)
        if not pdf_content:
            raise ValueError("PDF upload is empty.")

        paper_dir = self._paper_record_dir(resolved, arxiv_id)
        artifacts_dir = paper_dir / "artifacts"
        metadata_path = paper_dir / "paper.yaml"
        pdf_path = paper_dir / "paper.pdf"
        notes_path = artifacts_dir / "notes.md"

        previous_added_at = ""
        previous_tags: list[str] = []
        previous_starred = False
        previous_status = "unread"
        previous_status_updated_at = ""
        if metadata_path.exists():
            try:
                raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
                previous_added_at = str(raw.get("added_at") or "")
                previous_tags = self._normalize_tags(raw.get("tags") or [])
                previous_starred = bool(raw.get("starred", False))
                raw_status = str(raw.get("status") or "unread")
                previous_status = raw_status if raw_status in self._VALID_STATUSES else "unread"
                previous_status_updated_at = str(raw.get("status_updated_at") or "")
            except Exception:
                previous_added_at = ""
                previous_tags = []
                previous_starred = False
                previous_status = "unread"
                previous_status_updated_at = ""

        paper_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(pdf_content)
        if not notes_path.exists():
            self._atomic_write(notes_path, f"# {metadata.title or arxiv_id}\n")

        # Generate server-side page-1 render cache (WebP images + manifest)
        renders_dir = paper_dir / "renders"
        try:
            generate_page1_cache(pdf_path, renders_dir)
        except Exception:
            pass  # render failure should not block import

        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        source_url = metadata.url or ARXIV_ABS_URL.format(arxiv_id=arxiv_id)
        pdf_url = metadata.pdf_url or ARXIV_PDF_URL.format(arxiv_id=arxiv_id)
        record = PaperRecord(
            id=f"arxiv:{arxiv_id}",
            source="arxiv",
            arxiv_id=arxiv_id,
            title=metadata.title or arxiv_id,
            authors=metadata.authors,
            abstract=metadata.abstract,
            url=source_url,
            source_url=source_url,
            pdf_url=pdf_url,
            local_pdf_path=str(pdf_path.relative_to(self.paths.root)),
            local_metadata_path=str(metadata_path.relative_to(self.paths.root)),
            artifact_dir_path=str(artifacts_dir.relative_to(self.paths.root)),
            tags=previous_tags,
            starred=previous_starred,
            status=previous_status,
            status_updated_at=previous_status_updated_at,
            submitted_date=metadata.submitted_date,
            added_at=previous_added_at or now,
        )
        yaml_text = yaml.safe_dump(record.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        self._atomic_write(metadata_path, yaml_text)
        return record

    def list_papers(self, project: str) -> list[PaperRecord]:
        resolved = self._require_project(project)
        target_dir = self._paper_dir(resolved)
        if not target_dir.exists():
            return []

        records: list[PaperRecord] = []
        for path in sorted(target_dir.glob("arxiv-*/paper.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                records.append(PaperRecord.model_validate(raw))
            except Exception:
                continue
        return sorted(records, key=lambda item: (item.added_at, item.arxiv_id), reverse=True)

    def remove_paper(self, *, project: str, arxiv_id_or_url: str) -> PaperRecord:
        resolved = self._require_project(project)
        arxiv_id = self.parse_arxiv_id(arxiv_id_or_url)
        paper_dir = self._paper_record_dir(resolved, arxiv_id)
        metadata_path = paper_dir / "paper.yaml"
        if not paper_dir.exists():
            raise FileNotFoundError(f"Paper '{arxiv_id}' is not saved in project '{resolved}'.")

        record: PaperRecord | None = None
        if metadata_path.exists():
            raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
            record = PaperRecord.model_validate(raw)
        shutil.rmtree(paper_dir)
        if record is not None:
            return record
        return PaperRecord(
            id=f"arxiv:{arxiv_id}",
            source="arxiv",
            arxiv_id=arxiv_id,
            title=arxiv_id,
            url=ARXIV_ABS_URL.format(arxiv_id=arxiv_id),
            source_url=ARXIV_ABS_URL.format(arxiv_id=arxiv_id),
            html_url=ARXIV_HTML_URL.format(arxiv_id=arxiv_id),
            pdf_url=ARXIV_PDF_URL.format(arxiv_id=arxiv_id),
            added_at="",
        )

    def get_paper(self, *, project: str, paper_id: str) -> PaperRecord:
        resolved = self._require_project(project)
        arxiv_id = self._paper_id_to_arxiv_id(paper_id)
        metadata_path = self._metadata_path(resolved, arxiv_id)
        if not metadata_path.exists():
            raise FileNotFoundError(f"Paper '{paper_id}' is not saved in project '{resolved}'.")
        raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        return PaperRecord.model_validate(raw)

    def pdf_path(self, *, project: str, paper_id: str) -> Path:
        resolved = self._require_project(project)
        arxiv_id = self._paper_id_to_arxiv_id(paper_id)
        path = self._paper_record_dir(resolved, arxiv_id) / "paper.pdf"
        if not path.exists():
            raise FileNotFoundError(f"PDF for paper '{paper_id}' is not saved in project '{resolved}'.")
        return path

    def renders_dir(self, *, project: str, paper_id: str) -> Path:
        resolved = self._require_project(project)
        arxiv_id = self._paper_id_to_arxiv_id(paper_id)
        return self._paper_record_dir(resolved, arxiv_id) / "renders"

    def ensure_renders(self, *, project: str, paper_id: str) -> None:
        """Generate render cache if it doesn't exist yet."""
        renders = self.renders_dir(project=project, paper_id=paper_id)
        if is_manifest_current(load_manifest(renders)):
            return
        pdf = self.pdf_path(project=project, paper_id=paper_id)
        generate_page1_cache(pdf, renders)

    def toggle_star(self, *, project: str, paper_id: str) -> PaperRecord:
        resolved = self._require_project(project)
        arxiv_id = self._paper_id_to_arxiv_id(paper_id)
        metadata_path = self._metadata_path(resolved, arxiv_id)
        if not metadata_path.exists():
            raise FileNotFoundError(f"Paper '{paper_id}' is not saved in project '{resolved}'.")
        raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        raw["starred"] = not raw.get("starred", False)
        yaml_text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        self._atomic_write(metadata_path, yaml_text)
        return PaperRecord.model_validate(raw)

    _VALID_STATUSES = {"unread", "reading", "read"}

    def update_paper_status(self, *, project: str, paper_id: str, status: str) -> PaperRecord:
        if status not in self._VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(self._VALID_STATUSES))}")
        resolved = self._require_project(project)
        arxiv_id = self._paper_id_to_arxiv_id(paper_id)
        metadata_path = self._metadata_path(resolved, arxiv_id)
        if not metadata_path.exists():
            raise FileNotFoundError(f"Paper '{paper_id}' is not saved in project '{resolved}'.")
        raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        raw["status"] = status
        raw["status_updated_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
        yaml_text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        self._atomic_write(metadata_path, yaml_text)
        return PaperRecord.model_validate(raw)

    def update_paper_tags(self, *, project: str, paper_id: str, tags: list[str]) -> PaperRecord:
        resolved = self._require_project(project)
        arxiv_id = self._paper_id_to_arxiv_id(paper_id)
        metadata_path = self._metadata_path(resolved, arxiv_id)
        if not metadata_path.exists():
            raise FileNotFoundError(f"Paper '{paper_id}' is not saved in project '{resolved}'.")
        raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        raw["tags"] = self._normalize_tags(tags)
        yaml_text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        self._atomic_write(metadata_path, yaml_text)
        return PaperRecord.model_validate(raw)

    def backfill_submitted_dates(self, project: str) -> int:
        """Fetch submitted_date from arXiv API for papers that are missing it. Returns count of updated papers."""
        papers = self.list_papers(project)
        updated = 0
        for paper in papers:
            if paper.submitted_date:
                continue
            try:
                metadata = self._fetch_arxiv_metadata(paper.arxiv_id)
                submitted = metadata.get("submitted_date", "")
                if not submitted:
                    continue
                resolved = self._require_project(project)
                metadata_path = self._metadata_path(resolved, paper.arxiv_id)
                raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
                raw["submitted_date"] = submitted
                yaml_text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
                self._atomic_write(metadata_path, yaml_text)
                updated += 1
            except Exception:
                continue
        return updated

    @staticmethod
    def _normalize_tags(tags: object) -> list[str]:
        if not isinstance(tags, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            value = str(tag).strip().lower()
            if not value or value in seen:
                continue
            normalized.append(value)
            seen.add(value)
        return normalized

    def list_artifacts(self, *, project: str, paper_id: str) -> list[dict[str, object]]:
        resolved = self._require_project(project)
        arxiv_id = self._paper_id_to_arxiv_id(paper_id)
        artifacts_dir = self._paper_record_dir(resolved, arxiv_id) / "artifacts"
        if not artifacts_dir.exists():
            return []

        artifacts: list[dict[str, object]] = []
        for path in sorted(item for item in artifacts_dir.rglob("*") if item.is_file()):
            artifacts.append(
                {
                    "name": path.name,
                    "path": str(path.relative_to(self.paths.root)),
                    "relative_path": str(path.relative_to(artifacts_dir)),
                    "size_bytes": path.stat().st_size,
                }
            )
        return artifacts

    def get_note(self, *, project: str, paper_id: str) -> str:
        resolved = self._require_project(project)
        arxiv_id = self._paper_id_to_arxiv_id(paper_id)
        notes_path = self._paper_record_dir(resolved, arxiv_id) / "artifacts" / "notes.md"
        if not notes_path.exists():
            return ""
        return notes_path.read_text(encoding="utf-8")

    def save_note(self, *, project: str, paper_id: str, content: str) -> str:
        resolved = self._require_project(project)
        arxiv_id = self._paper_id_to_arxiv_id(paper_id)
        notes_path = self._paper_record_dir(resolved, arxiv_id) / "artifacts" / "notes.md"
        if not notes_path.parent.exists():
            notes_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(notes_path, content)
        return content

    def parse_arxiv_id(self, reference: str) -> str:
        value = reference.strip()
        if not value:
            raise ValueError("Usage: /paper add <arxiv-id-or-url>")
        match = ARXIV_ID_RE.search(value)
        if not match:
            raise ValueError("Only arXiv IDs and arXiv URLs are supported for now.")
        return match.group("id")

    def _fetch_metadata(self, arxiv_id: str) -> dict:
        """Try multiple metadata sources before giving up.

        The arXiv API is easy to rate limit during interactive retries, while
        the public abs page is often still reachable. Keep the abs-page parser
        as the final fallback so a single paper can still be saved locally.
        """
        failures: list[str] = []
        try:
            return self._fetch_s2_metadata(arxiv_id)
        except Exception as exc:
            failures.append(f"Semantic Scholar: {exc}")
        try:
            return self._fetch_arxiv_metadata(arxiv_id)
        except Exception as exc:
            failures.append(f"arXiv API: {exc}")
        try:
            return self._fetch_abs_metadata(arxiv_id)
        except Exception as exc:
            failures.append(f"arXiv abs page: {exc}")
        raise RuntimeError("Could not fetch paper metadata.\n" + "\n".join(failures))

    def _fetch_s2_metadata(self, arxiv_id: str) -> dict:
        url = S2_API_URL.format(arxiv_id=arxiv_id) + f"?fields={S2_FIELDS}"
        body = self._fetch_text(url, accept="application/json")
        data = json.loads(body)
        title = data.get("title") or arxiv_id
        abstract = data.get("abstract") or ""
        authors = [a.get("name", "") for a in (data.get("authors") or [])]
        authors = [a for a in authors if a]
        pub_date = (data.get("publicationDate") or "")[:10]  # "YYYY-MM-DD"
        return {"title": title, "authors": authors, "abstract": abstract, "submitted_date": pub_date}

    def _fetch_arxiv_metadata(self, arxiv_id: str) -> dict:
        query = urllib.parse.urlencode({"id_list": arxiv_id})
        url = f"{ARXIV_API_URL}?{query}"
        body = self._fetch_text(url, accept="application/atom+xml")
        root = ET.fromstring(body)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            raise ValueError(f"arXiv paper '{arxiv_id}' was not found.")

        title = self._xml_text(entry.find("atom:title", ns))
        abstract = self._xml_text(entry.find("atom:summary", ns))
        authors = [
            self._xml_text(author.find("atom:name", ns))
            for author in entry.findall("atom:author", ns)
        ]
        authors = [author for author in authors if author]
        if not title:
            title = arxiv_id
        # <published> is the v1 submission date
        published = self._xml_text(entry.find("atom:published", ns))[:10]  # "YYYY-MM-DD"
        return {
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "submitted_date": published,
        }

    def _fetch_abs_metadata(self, arxiv_id: str) -> dict:
        url = ARXIV_ABS_URL.format(arxiv_id=arxiv_id)
        body = self._fetch_text(url, accept="text/html")
        title = self._extract_html_text(body, r'<h1 class="title mathjax">(.*?)</h1>')
        abstract = self._extract_html_text(
            body,
            r'<blockquote class="abstract mathjax">(.*?)</blockquote>',
        )
        authors_text = self._extract_html_text(body, r'<div class="authors">(.*?)</div>')

        title = re.sub(r"^Title:\s*", "", title).strip() or arxiv_id
        abstract = re.sub(r"^Abstract:\s*", "", abstract).strip()
        authors_text = re.sub(r"^Authors?:\s*", "", authors_text).strip()
        authors = [item.strip() for item in re.split(r"\s*,\s*", authors_text) if item.strip()]
        # Try to extract submission date from the abs page dateline
        dateline = self._extract_html_text(body, r'<div class="dateline">(.*?)</div>')
        submitted_match = re.search(r"\b(\d{1,2}\s+\w+\s+\d{4})\b", dateline)
        submitted_date = ""
        if submitted_match:
            try:
                from datetime import datetime as _dt
                submitted_date = _dt.strptime(submitted_match.group(1), "%d %b %Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
        return {
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "submitted_date": submitted_date,
        }

    def _fetch_html(self, arxiv_id: str) -> tuple[str, str]:
        # Try ar5iv first — it's a mirror with less strict rate limiting
        urls = [
            AR5IV_HTML_URL.format(arxiv_id=arxiv_id),
            ARXIV_HTML_URL.format(arxiv_id=arxiv_id),
        ]
        failures: list[str] = []
        for url in urls:
            try:
                return self._fetch_text(url, accept="text/html"), url
            except Exception as exc:
                failures.append(f"{url}: {exc}")
        raise RuntimeError("Could not fetch HTML for this arXiv paper.\n" + "\n".join(failures))

    def _fetch_text(self, url: str, *, accept: str) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": accept,
                "User-Agent": "labit/0.1 (+https://github.com/qinglinh2003)",
            },
        )
        # At most 1 retry — don't hammer arXiv
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return response.read().decode(charset, errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    if attempt == 0:
                        delay = self._retry_delay(exc)
                        sleep(delay)
                        continue
                    host = urllib.parse.urlparse(url).netloc or url
                    raise RuntimeError(
                        f"{host} is rate-limiting requests. Wait a few minutes and try again."
                    ) from exc
                if exc.code == 503 and attempt == 0:
                    sleep(5)
                    continue
                raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                reason = getattr(exc, "reason", exc)
                if attempt == 0:
                    sleep(5)
                    continue
                raise RuntimeError(f"Connection failed: {reason}") from exc

        raise RuntimeError(f"Failed to fetch {url}")

    def _retry_delay(self, exc: urllib.error.HTTPError) -> float:
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
        return 5.0

    def _xml_text(self, node: ET.Element | None) -> str:
        if node is None or node.text is None:
            return ""
        return " ".join(node.text.split())

    def _extract_html_text(self, body: str, pattern: str) -> str:
        match = re.search(pattern, body, flags=re.DOTALL | re.IGNORECASE)
        if match is None:
            return ""
        text = re.sub(r"<[^>]+>", " ", match.group(1))
        return " ".join(unescape(text).split())

    def _require_project(self, project: str) -> str:
        resolved = self.project_service.resolve_project_name(project)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{project}' not found. Available projects: {', '.join(self.project_service.list_project_names()) or '(none)'}"
            )
        return resolved

    def _paper_dir(self, project: str) -> Path:
        return self.paths.vault_projects_dir / project / "papers"

    def _paper_record_dir(self, project: str, arxiv_id: str) -> Path:
        return self._paper_dir(project) / f"arxiv-{arxiv_id}"

    def _metadata_path(self, project: str, arxiv_id: str) -> Path:
        return self._paper_record_dir(project, arxiv_id) / "paper.yaml"

    def _paper_id_to_arxiv_id(self, paper_id: str) -> str:
        value = paper_id.strip()
        if value.startswith("arxiv:"):
            return self.parse_arxiv_id(value.removeprefix("arxiv:"))
        if value.startswith("arxiv-"):
            return self.parse_arxiv_id(value.removeprefix("arxiv-"))
        return self.parse_arxiv_id(value)

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
