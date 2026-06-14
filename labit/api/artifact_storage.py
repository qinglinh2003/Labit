"""Shared artifact utilities: extraction from agent output + file persistence."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from labit.api.chat_models import Artifact


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    """Remove path traversal and special characters from a filename."""
    name = name.strip().replace("\\", "/")
    name = name.split("/")[-1]  # take only the basename
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "", name)
    return name or "artifact.txt"


def _unique_filename(artifacts_dir: Path, filename: str) -> str:
    """Return a unique filename within the directory, adding _2, _3, etc. if needed."""
    if not (artifacts_dir / filename).exists():
        return filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while (artifacts_dir / f"{stem}_{counter}{suffix}").exists():
        counter += 1
    return f"{stem}_{counter}{suffix}"


# ---------------------------------------------------------------------------
# Artifact extraction from agent text
# ---------------------------------------------------------------------------

_ARTIFACT_OPEN_RE = re.compile(
    r"(`{3,})\s*artifact:\s*([^\n]+)\n?"
)

_EXT_TO_LANG: dict[str, str] = {
    ".md": "markdown", ".markdown": "markdown",
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".sh": "bash", ".bash": "bash",
    ".tex": "latex", ".css": "css", ".html": "html",
    ".sql": "sql", ".rs": "rust", ".go": "go",
    ".java": "java", ".c": "c", ".cpp": "cpp",
    ".txt": "text", ".csv": "csv", ".xml": "xml",
}

_EXT_TO_MIME: dict[str, str] = {
    ".md": "text/markdown", ".markdown": "text/markdown",
    ".py": "text/x-python", ".js": "text/javascript",
    ".ts": "text/typescript", ".tsx": "text/typescript",
    ".json": "application/json", ".yaml": "text/yaml", ".yml": "text/yaml",
    ".toml": "text/toml", ".sh": "text/x-shellscript",
    ".tex": "text/x-latex", ".css": "text/css",
    ".html": "text/plain",  # serve as plain text for safety
    ".sql": "text/x-sql", ".csv": "text/csv", ".xml": "text/xml",
    ".txt": "text/plain",
}


def extract_artifacts(text: str, agent: str = "") -> tuple[str, list[Artifact]]:
    """Extract artifact blocks from agent output.

    Returns (cleaned_text, artifacts) where cleaned_text has artifact blocks
    replaced with placeholder references.

    The parser counts the backticks in the opening fence and only closes when
    it finds a line with *exactly* that many backticks (and nothing else).
    This correctly handles artifacts that contain nested code blocks with
    fewer backticks.
    """
    artifacts: list[Artifact] = []
    result_parts: list[str] = []
    pos = 0

    while pos < len(text):
        m = _ARTIFACT_OPEN_RE.search(text, pos)
        if m is None:
            result_parts.append(text[pos:])
            break

        # Text before the artifact block
        result_parts.append(text[pos:m.start()])

        fence = m.group(1)  # the backtick sequence (e.g. ````` or ```)
        raw_filename = _sanitize_filename(m.group(2).strip())
        title = Path(raw_filename).stem.replace("_", " ").replace("-", " ").strip() or raw_filename
        content_start = m.end()

        # Metadata is preferred but optional for backward compatibility with
        # older prompts and imperfect model outputs.
        metadata_m = re.match(r"title:\s*([^\n]+)\n---\s*\n", text[content_start:])
        if metadata_m:
            title = metadata_m.group(1).strip()
            content_start += metadata_m.end()

        # Find closing fence: the same backtick sequence on its own line
        # (preceded by newline) or directly after content at the very end
        # of the text (agents sometimes omit the newline before closing).
        # We try newline-preceded first, falling back to end-of-text match.
        closing_nl = re.compile(r"\n" + re.escape(fence) + r"\s*(?:\n|$)")
        close_m = closing_nl.search(text, content_start)
        if close_m is None:
            # Fallback: fence at end of text without preceding newline
            closing_end = re.compile(re.escape(fence) + r"\s*$")
            close_m = closing_end.search(text, content_start)

        if close_m is None:
            # No closing fence found — treat as plain text
            result_parts.append(text[m.start():m.end()])
            pos = m.end()
            continue

        content = text[content_start:close_m.start()]
        # Strip trailing newline from content if the closing fence was on its own line
        if close_m.group(0).startswith("\n"):
            pass  # newline is already excluded from content via close_m.start()
        # else: content may end with the last text char before the fence
        ext = Path(raw_filename).suffix.lower()
        language = _EXT_TO_LANG.get(ext, "text")
        mime_type = _EXT_TO_MIME.get(ext, "text/plain")

        art = Artifact(
            id=f"art_{uuid.uuid4().hex[:8]}",
            title=title,
            filename=raw_filename,
            language=language,
            mime_type=mime_type,
            content=content,
        )
        artifacts.append(art)
        result_parts.append(f"[Artifact: {title} ({raw_filename})]")
        pos = close_m.end()

    return "".join(result_parts), artifacts


# ---------------------------------------------------------------------------
# Artifact file persistence
# ---------------------------------------------------------------------------

def write_artifact_file(chat_dir: Path, artifact) -> str:
    """Write artifact content to chat_dir/artifacts/{filename}.

    Returns the relative path (e.g. "artifacts/proposal.md") and updates
    artifact.file_path in place.
    """
    artifacts_dir = chat_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _sanitize_filename(artifact.filename)
    unique_name = _unique_filename(artifacts_dir, safe_name)

    dest = artifacts_dir / unique_name
    dest.write_text(artifact.content, encoding="utf-8")

    rel_path = f"artifacts/{unique_name}"
    artifact.file_path = rel_path
    return rel_path
