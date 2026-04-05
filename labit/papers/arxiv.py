from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_ABS_URL = "https://arxiv.org/abs/{identifier}"
ARXIV_HTML_URL = "https://arxiv.org/html/{identifier}"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{identifier}.pdf"
USER_AGENT = "labit/0.1 (+https://github.com/qinglinh2003/Research-OS)"

ARXIV_ID_PATTERN = re.compile(
    r"(?P<id>(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)",
    re.IGNORECASE,
)


class ArxivResolutionError(RuntimeError):
    """Raised when an arXiv reference cannot be resolved."""


@dataclass
class ArxivPaperSource:
    raw_ref: str
    canonical_paper_id: str
    arxiv_id: str
    versioned_id: str
    title: str
    authors: list[str]
    abstract: str
    year: int | None
    published_at: str | None
    abs_url: str
    html_url: str
    pdf_url: str


def extract_arxiv_identifier(reference: str) -> str | None:
    reference = reference.strip()
    if not reference:
        return None

    parsed = urllib.parse.urlparse(reference)
    candidate = reference
    if parsed.scheme and parsed.netloc:
        path = parsed.path.strip("/")
        if path.startswith("abs/"):
            candidate = path.removeprefix("abs/")
        elif path.startswith("pdf/"):
            candidate = path.removeprefix("pdf/")
        elif path.startswith("html/"):
            candidate = path.removeprefix("html/")
        else:
            candidate = path
        candidate = candidate.removesuffix(".pdf")

    match = ARXIV_ID_PATTERN.search(candidate)
    if match is None:
        return None
    return match.group("id")


def canonical_arxiv_paper_id(identifier: str) -> str:
    return f"arxiv:{strip_arxiv_version(identifier)}"


def strip_arxiv_version(identifier: str) -> str:
    return re.sub(r"v\d+$", "", identifier)


class ArxivClient:
    _MIN_INTERVAL = 3.0  # seconds between requests (arXiv policy)

    def __init__(self) -> None:
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        """Sleep if needed so consecutive requests are ≥ _MIN_INTERVAL apart."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._MIN_INTERVAL:
            time.sleep(self._MIN_INTERVAL - elapsed)

    def _urlopen(self, url: str, *, timeout: int = 30) -> bytes:
        self._throttle()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            self._last_request_at = time.monotonic()
            return response.read()

    def search(self, query: str, *, max_results: int = 10, sort_by: str = "relevance") -> list[ArxivPaperSource]:
        query = query.strip()
        if not query:
            raise ArxivResolutionError("Search query cannot be empty.")

        encoded = urllib.parse.quote(query)
        url = (
            f"{ARXIV_API_URL}?search_query=all:{encoded}"
            f"&start=0&max_results={max_results}&sortBy={sort_by}&sortOrder=descending"
        )
        try:
            payload = self._urlopen(url)
        except urllib.error.URLError as exc:
            raise ArxivResolutionError(f"Failed to search arXiv: {exc}") from exc

        root = ET.fromstring(payload)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        return [self._parse_entry(entry, query) for entry in entries]

    def resolve(self, reference: str) -> ArxivPaperSource:
        identifier = extract_arxiv_identifier(reference)
        if identifier is None:
            raise ArxivResolutionError(
                "Only arXiv ids and arXiv URLs are supported right now."
            )

        url = f"{ARXIV_API_URL}?id_list={urllib.parse.quote(identifier)}"
        try:
            payload = self._urlopen(url)
        except urllib.error.URLError as exc:
            raise ArxivResolutionError(f"Failed to fetch arXiv metadata: {exc}") from exc

        root = ET.fromstring(payload)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            raise ArxivResolutionError(f"No arXiv entry found for '{reference}'.")

        return self._parse_entry(entry, reference)

    def download_html(self, source: ArxivPaperSource) -> str | None:
        try:
            data, content_type = self._download_bytes(source.html_url)
        except ArxivResolutionError:
            return None
        if "html" not in content_type.lower() and not data.lstrip().startswith(b"<"):
            return None
        return data.decode("utf-8", errors="replace")

    def download_pdf(self, source: ArxivPaperSource) -> bytes | None:
        try:
            data, content_type = self._download_bytes(source.pdf_url)
        except ArxivResolutionError:
            return None
        if "pdf" not in content_type.lower() and not data.startswith(b"%PDF"):
            return None
        return data

    def _download_bytes(self, url: str) -> tuple[bytes, str]:
        self._throttle()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                self._last_request_at = time.monotonic()
                return response.read(), response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            raise ArxivResolutionError(f"Download failed for {url}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ArxivResolutionError(f"Download failed for {url}: {exc}") from exc

    def _entry_text(self, entry: ET.Element, path: str, ns: dict[str, str]) -> str:
        node = entry.find(path, ns)
        if node is None or node.text is None:
            return ""
        return node.text.strip()

    def _parse_entry(self, entry: ET.Element, raw_ref: str) -> ArxivPaperSource:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry_id = self._entry_text(entry, "atom:id", ns)
        versioned_id = entry_id.rsplit("/", 1)[-1] if entry_id else raw_ref
        base_id = strip_arxiv_version(versioned_id)
        title = self._entry_text(entry, "atom:title", ns).replace("\n", " ").strip()
        abstract = self._entry_text(entry, "atom:summary", ns).replace("\n", " ").strip()
        authors = [
            node.text.strip()
            for node in entry.findall("atom:author/atom:name", ns)
            if node.text and node.text.strip()
        ]
        published = self._entry_text(entry, "atom:published", ns)
        year = int(published[:4]) if published and len(published) >= 4 else None

        return ArxivPaperSource(
            raw_ref=raw_ref,
            canonical_paper_id=canonical_arxiv_paper_id(base_id),
            arxiv_id=base_id,
            versioned_id=versioned_id,
            title=title,
            authors=authors,
            abstract=abstract,
            year=year,
            published_at=published or None,
            abs_url=ARXIV_ABS_URL.format(identifier=versioned_id),
            html_url=ARXIV_HTML_URL.format(identifier=versioned_id),
            pdf_url=ARXIV_PDF_URL.format(identifier=versioned_id),
        )
