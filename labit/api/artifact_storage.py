"""Shared utility for writing artifact files to per-chat artifact directories."""
from __future__ import annotations

import re
from pathlib import Path


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
