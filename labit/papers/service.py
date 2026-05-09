from __future__ import annotations

import json
import re
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

from labit.papers.models import PaperRecord
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_ABS_URL = "https://arxiv.org/abs/{arxiv_id}"
ARXIV_HTML_URL = "https://arxiv.org/html/{arxiv_id}"
AR5IV_HTML_URL = "https://ar5iv.labs.arxiv.org/html/{arxiv_id}"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
S2_API_URL = "https://api.semanticscholar.org/graph/v1/paper/ARXIV:{arxiv_id}"
S2_FIELDS = "title,authors,abstract,externalIds,url"
ARXIV_ID_RE = re.compile(r"(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)")


class PaperService:
    def __init__(self, paths: RepoPaths, *, project_service: ProjectService | None = None):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)

    def add_paper(self, *, project: str, reference: str) -> PaperRecord:
        resolved = self._require_project(project)
        arxiv_id = self.parse_arxiv_id(reference)

        # Cache-first: if we already have metadata locally, return it
        target_dir = self._paper_dir(resolved)
        metadata_path = target_dir / f"{arxiv_id}.yaml"
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
            html_fetch_error=html_fetch_error,
            pdf_url=ARXIV_PDF_URL.format(arxiv_id=arxiv_id),
            local_html_path=str(html_path.relative_to(self.paths.root)) if html else "",
            local_metadata_path=str(metadata_path.relative_to(self.paths.root)),
            added_at=now,
        )

        if html:
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
        return {"title": title, "authors": authors, "abstract": abstract}

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
        return {
            "title": title,
            "authors": authors,
            "abstract": abstract,
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

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
