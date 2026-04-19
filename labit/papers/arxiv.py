from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_ABS_URL = "https://arxiv.org/abs/{identifier}"
ARXIV_HTML_URL = "https://arxiv.org/html/{identifier}"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{identifier}.pdf"
DEFAULT_USER_AGENT = "labit/0.1 (+https://github.com/qinglinh2003/Research-OS)"

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
    _MAX_RETRIES = 5
    _CACHE_FILENAME = "arxiv.json"

    def __init__(self) -> None:
        self._last_request_at = 0.0
        self._cache_path = self._resolve_cache_path()
        self._cache = self._load_cache()

    def _resolve_cache_path(self) -> Path:
        override = os.environ.get("LABIT_ARXIV_CACHE")
        if override:
            return Path(override).expanduser()
        cache_dir = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser()
        return cache_dir / "labit" / self._CACHE_FILENAME

    def _load_cache(self) -> dict[str, dict]:
        try:
            if self._cache_path.exists():
                return json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {}

    def _save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._cache_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self._cache_path)
        except OSError:
            # Cache failures should never break ingestion.
            return

    def _throttle(self) -> None:
        """Sleep if needed so consecutive requests are ≥ _MIN_INTERVAL apart."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._MIN_INTERVAL:
            time.sleep(self._MIN_INTERVAL - elapsed)

    def _user_agent(self) -> str:
        contact = os.environ.get("LABIT_ARXIV_CONTACT")
        if contact:
            return f"{DEFAULT_USER_AGENT} (contact: {contact})"
        return DEFAULT_USER_AGENT

    def _urlopen(self, url: str, *, timeout: int = 30) -> bytes:
        last_exc: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            self._throttle()
            request = urllib.request.Request(url, headers={"User-Agent": self._user_agent()})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    self._last_request_at = time.monotonic()
                    return response.read()
            except urllib.error.HTTPError as exc:
                last_exc = exc
                retry_after = exc.headers.get("Retry-After")
                if exc.code in {429, 500, 502, 503, 504}:
                    self._sleep_backoff(attempt, retry_after)
                    continue
                raise
            except urllib.error.URLError as exc:
                last_exc = exc
                self._sleep_backoff(attempt, None)
                continue
        if last_exc is None:
            raise urllib.error.URLError("Unknown arXiv request failure")
        raise last_exc

    def _sleep_backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = max(1.0, float(retry_after))
            except ValueError:
                delay = 0.0
        else:
            delay = 2.0 * (2 ** attempt)
        # Jitter to avoid synchronized retries on shared IPs.
        delay += random.uniform(0.0, 0.5)
        time.sleep(delay)

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

        cache_key = strip_arxiv_version(identifier)
        cached = self._cache.get(cache_key)
        if isinstance(cached, dict):
            try:
                return ArxivPaperSource(**cached)
            except TypeError:
                pass

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

        source = self._parse_entry(entry, reference)
        self._cache[cache_key] = source.__dict__.copy()
        self._save_cache()
        return source

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
        last_exc: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            self._throttle()
            request = urllib.request.Request(url, headers={"User-Agent": self._user_agent()})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    self._last_request_at = time.monotonic()
                    return response.read(), response.headers.get_content_type()
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code in {429, 500, 502, 503, 504}:
                    self._sleep_backoff(attempt, exc.headers.get("Retry-After"))
                    continue
                raise ArxivResolutionError(f"Download failed for {url}: HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                last_exc = exc
                self._sleep_backoff(attempt, None)
                continue
        raise ArxivResolutionError(f"Download failed for {url}: {last_exc}") from last_exc

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
