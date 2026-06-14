"""Shared attachment utilities: MIME helpers, upload/resolve for chat attachments."""
from __future__ import annotations

import uuid
from pathlib import Path

from labit.api.chat_models import Attachment


ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def mime_to_ext(mime_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime_type, ".bin")


def ext_to_mime(ext: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext.lower(), "application/octet-stream")


def upload_attachment(
    attachments_dir: Path,
    filename: str,
    mime_type: str,
    data: bytes,
) -> Attachment:
    """Save an uploaded file and return its Attachment metadata."""
    attachments_dir.mkdir(parents=True, exist_ok=True)
    att_id = uuid.uuid4().hex[:12]
    ext = mime_to_ext(mime_type)
    dest = attachments_dir / f"{att_id}{ext}"
    dest.write_bytes(data)
    return Attachment(
        id=att_id,
        kind="image",
        filename=filename,
        mime_type=mime_type,
        path=str(dest),
    )


def get_attachment_path(attachments_dir: Path, att_id: str) -> Path | None:
    """Return the file path of an attachment, or None if not found."""
    if not attachments_dir.exists():
        return None
    for f in attachments_dir.iterdir():
        if f.stem == att_id:
            return f
    return None


def resolve_attachment_ids(
    attachments_dir: Path,
    messages: list,
    attachment_ids: list[str],
) -> list[Attachment]:
    """Look up stored attachment metadata by IDs from messages + disk.

    Args:
        attachments_dir: Directory where attachment files are stored.
        messages: List of chat message objects (must have .attachments).
        attachment_ids: IDs to resolve.
    """
    by_id: dict[str, Attachment] = {}
    for msg in messages:
        for att in msg.attachments:
            by_id[att.id] = att
    # Also check files on disk for recently uploaded but not yet in a message
    if attachments_dir.exists():
        for f in attachments_dir.iterdir():
            if f.stem not in by_id:
                by_id[f.stem] = Attachment(
                    id=f.stem,
                    kind="image",
                    filename=f.name,
                    mime_type=ext_to_mime(f.suffix),
                    path=str(f),
                )
    return [by_id[aid] for aid in attachment_ids if aid in by_id]
