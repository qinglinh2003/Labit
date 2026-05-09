from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml

from labit.papers.models import PaperRecord
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_ABS_URL = "https://arxiv.org/abs/{arxiv_id}"
ARXIV_HTML_URL = "https://arxiv.org/html/{arxiv_id}"
AR5IV_HTML_URL = "https://ar5iv.labs.arxiv.org/html/{arxiv_id}"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
ARXIV_ID_RE = re.compile(r"(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)")


class PaperService:
    def __init__(self, paths: RepoPaths, *, project_service: ProjectService | None = None):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)

    def add_paper(self, *, project: str, reference: str) -> PaperRecord:
        resolved = self._require_project(project)
        arxiv_id = self.parse_arxiv_id(reference)
        metadata = self._fetch_arxiv_metadata(arxiv_id)
        html, html_url = self._fetch_html(arxiv_id)

        target_dir = self._paper_dir(resolved)
        target_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = target_dir / f"{arxiv_id}.yaml"
        html_path = target_dir / f"{arxiv_id}.html"

        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        record = PaperRecord(
            arxiv_id=arxiv_id,
            title=metadata["title"],
            authors=metadata["authors"],
            abstract=metadata["abstract"],
            source_url=ARXIV_ABS_URL.format(arxiv_id=arxiv_id),
            html_url=html_url,
            pdf_url=ARXIV_PDF_URL.format(arxiv_id=arxiv_id),
            local_html_path=str(html_path.relative_to(self.paths.root)),
            local_metadata_path=str(metadata_path.relative_to(self.paths.root)),
            added_at=now,
        )

        self._atomic_write(html_path, html)
        yaml_text = yaml.safe_dump(record.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        self._atomic_write(metadata_path, yaml_text)
        return record

    def list_papers(self, project: str) -> list[PaperRecord]:
        resolved = self._require_project(project)
        target_dir = self._paper_dir(resolved)
        if not target_dir.exists():
            return []

        records: list[PaperRecord] = []
        for path in sorted(target_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                records.append(PaperRecord.model_validate(raw))
            except Exception:
                continue
        return sorted(records, key=lambda item: (item.added_at, item.arxiv_id), reverse=True)

    def remove_paper(self, *, project: str, arxiv_id_or_url: str) -> PaperRecord:
        resolved = self._require_project(project)
        arxiv_id = self.parse_arxiv_id(arxiv_id_or_url)
        target_dir = self._paper_dir(resolved)
        metadata_path = target_dir / f"{arxiv_id}.yaml"
        html_path = target_dir / f"{arxiv_id}.html"
        if not metadata_path.exists() and not html_path.exists():
            raise FileNotFoundError(f"Paper '{arxiv_id}' is not saved in project '{resolved}'.")

        record: PaperRecord | None = None
        if metadata_path.exists():
            raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
            record = PaperRecord.model_validate(raw)
            metadata_path.unlink()
        if html_path.exists():
            html_path.unlink()
        if record is not None:
            return record
        return PaperRecord(
            arxiv_id=arxiv_id,
            title=arxiv_id,
            source_url=ARXIV_ABS_URL.format(arxiv_id=arxiv_id),
            html_url=ARXIV_HTML_URL.format(arxiv_id=arxiv_id),
            pdf_url=ARXIV_PDF_URL.format(arxiv_id=arxiv_id),
            added_at="",
        )

    def parse_arxiv_id(self, reference: str) -> str:
        value = reference.strip()
        if not value:
            raise ValueError("Usage: /paper add <arxiv-id-or-url>")
        match = ARXIV_ID_RE.search(value)
        if not match:
            raise ValueError("Only arXiv IDs and arXiv URLs are supported for now.")
        return match.group("id")

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
        return {
            "title": title,
            "authors": authors,
            "abstract": abstract,
        }

    def _fetch_html(self, arxiv_id: str) -> tuple[str, str]:
        urls = [
            ARXIV_HTML_URL.format(arxiv_id=arxiv_id),
            AR5IV_HTML_URL.format(arxiv_id=arxiv_id),
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
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc

    def _xml_text(self, node: ET.Element | None) -> str:
        if node is None or node.text is None:
            return ""
        return " ".join(node.text.split())

    def _require_project(self, project: str) -> str:
        resolved = self.project_service.resolve_project_name(project)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{project}' not found. Available projects: {', '.join(self.project_service.list_project_names()) or '(none)'}"
            )
        return resolved

    def _paper_dir(self, project: str) -> Path:
        return self.paths.vault_projects_dir / project / "papers"

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
