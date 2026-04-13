"""Mine Labit artifacts into a MemPalace ChromaDB palace.

Ingests high-signal files: documents, hypotheses, papers, experiment plans.
Does NOT ingest raw session events (too noisy).
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
MIN_CHUNK_SIZE = 50

READABLE_EXTENSIONS = {".md", ".yaml", ".yml", ".txt", ".py", ".sh"}


def _chunk_text(content: str) -> list[dict]:
    content = content.strip()
    if not content:
        return []
    chunks = []
    start = 0
    idx = 0
    while start < len(content):
        end = min(start + CHUNK_SIZE, len(content))
        if end < len(content):
            nl = content.rfind("\n\n", start, end)
            if nl > start + CHUNK_SIZE // 2:
                end = nl
            else:
                nl = content.rfind("\n", start, end)
                if nl > start + CHUNK_SIZE // 2:
                    end = nl
        chunk = content[start:end].strip()
        if len(chunk) >= MIN_CHUNK_SIZE:
            chunks.append({"content": chunk, "chunk_index": idx})
            idx += 1
        start = end - CHUNK_OVERLAP if end < len(content) else end
    return chunks


def _detect_room(filepath: Path, project_dir: Path) -> str:
    relative = str(filepath.relative_to(project_dir)).lower()
    if "hypothes" in relative:
        return "hypotheses"
    if "doc" in relative and "design" in relative:
        return "designs"
    if "doc" in relative:
        return "documents"
    if "experiment" in relative:
        return "experiments"
    if "paper" in relative or "summary" in relative:
        return "papers"
    if "idea" in relative or "todo" in relative:
        return "ideas"
    if "memory" in relative:
        return "memory"
    return "general"


def _file_already_mined(collection, source_file: str) -> bool:
    try:
        results = collection.get(where={"source_file": source_file}, limit=1)
        if not results.get("ids"):
            return False
        stored_meta = results.get("metadatas", [{}])[0]
        stored_mtime = stored_meta.get("source_mtime")
        if stored_mtime is None:
            return False
        current_mtime = os.path.getmtime(source_file)
        return abs(float(stored_mtime) - current_mtime) < 0.001
    except Exception:
        return False


def mine_project(
    project_name: str,
    project_dir: Path,
    palace_path: Path,
    *,
    dry_run: bool = False,
) -> dict:
    """Mine a Labit project's artifacts into the palace.

    Returns stats dict with files_processed, drawers_filed, etc.
    """
    try:
        import chromadb
    except ImportError:
        raise RuntimeError(
            "chromadb is not installed. Install with: pip install 'labit[mempalace]'"
        )

    palace_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(palace_path))
    collection = client.get_or_create_collection("mempalace_drawers")

    wing = project_name

    # Collect minable files from project directory
    files: list[Path] = []
    for root, dirs, filenames in os.walk(project_dir):
        root_path = Path(root)
        # Skip hidden dirs, __pycache__, etc
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for fn in filenames:
            fp = root_path / fn
            if fp.suffix.lower() in READABLE_EXTENSIONS:
                try:
                    if fp.stat().st_size > 5 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                files.append(fp)

    # Also mine from vault papers (summaries)
    papers_dir = project_dir.parent.parent / "papers" / "by_id"
    if papers_dir.is_dir():
        for paper_dir in papers_dir.iterdir():
            if not paper_dir.is_dir():
                continue
            summary = paper_dir / "summary.md"
            if summary.is_file():
                files.append(summary)

    stats = {
        "wing": wing,
        "total_files": len(files),
        "files_processed": 0,
        "files_skipped": 0,
        "drawers_filed": 0,
        "rooms": defaultdict(int),
    }

    for filepath in files:
        source_file = str(filepath)
        if not dry_run and _file_already_mined(collection, source_file):
            stats["files_skipped"] += 1
            continue

        try:
            content = filepath.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue

        if len(content) < MIN_CHUNK_SIZE:
            continue

        room = _detect_room(filepath, project_dir)
        chunks = _chunk_text(content)

        if dry_run:
            stats["files_processed"] += 1
            stats["drawers_filed"] += len(chunks)
            stats["rooms"][room] += 1
            continue

        # Delete stale drawers for this file before reinserting
        try:
            collection.delete(where={"source_file": source_file})
        except Exception:
            pass

        for chunk in chunks:
            drawer_id = (
                f"drawer_{wing}_{room}_"
                f"{hashlib.sha256((source_file + str(chunk['chunk_index'])).encode()).hexdigest()[:24]}"
            )
            metadata = {
                "wing": wing,
                "room": room,
                "source_file": source_file,
                "chunk_index": chunk["chunk_index"],
                "added_by": "labit",
                "filed_at": datetime.now().isoformat(),
            }
            try:
                metadata["source_mtime"] = os.path.getmtime(source_file)
            except OSError:
                pass
            collection.upsert(
                documents=[chunk["content"]],
                ids=[drawer_id],
                metadatas=[metadata],
            )
            stats["drawers_filed"] += 1

        stats["files_processed"] += 1
        stats["rooms"][room] += 1

    return stats
