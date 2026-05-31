"""PDF text extraction and caching via PyMuPDF."""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


def extract_text(pdf_path: Path) -> str:
    """Extract full text from a PDF file."""
    doc = fitz.open(pdf_path)
    pages: list[str] = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages)


def ensure_text_cache(pdf_path: Path, artifacts_dir: Path) -> Path:
    """Extract text from PDF and cache to artifacts/text/full.txt.

    Returns the path to the cached text file.
    """
    text_dir = artifacts_dir / "text"
    text_file = text_dir / "full.txt"
    if text_file.exists():
        return text_file
    text_dir.mkdir(parents=True, exist_ok=True)
    text = extract_text(pdf_path)
    text_file.write_text(text, encoding="utf-8")
    return text_file


def get_cached_text(artifacts_dir: Path) -> str | None:
    """Read cached text if available."""
    text_file = artifacts_dir / "text" / "full.txt"
    if text_file.exists():
        return text_file.read_text(encoding="utf-8")
    return None
