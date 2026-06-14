"""Generic chat storage helpers shared by all chat services.

Each service resolves its own ``chats_dir`` and model types, then delegates
the common IO/CRUD operations to these helpers.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from labit.api.chat_models import AgentName, ChatMode

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Low-level path helpers
# ---------------------------------------------------------------------------

def chat_path(chats_dir: Path, chat_id: str) -> Path:
    """Locate a chat JSON file, preferring new dir format over legacy flat file."""
    new_path = chats_dir / chat_id / "chat.json"
    if new_path.exists():
        return new_path
    old_path = chats_dir / f"{chat_id}.json"
    if old_path.exists():
        return old_path
    return new_path


def save_chat(chats_dir: Path, record: BaseModel) -> None:
    """Persist a chat record as ``chats/{chat_id}/chat.json``."""
    chat_id: str = record.chat_id  # type: ignore[attr-defined]
    chat_dir = chats_dir / chat_id
    chat_dir.mkdir(parents=True, exist_ok=True)
    path = chat_dir / "chat.json"
    path.write_text(record.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def list_chat_records(chats_dir: Path, record_cls: type[T]) -> list[T]:
    """Load all chat records from *chats_dir* (both formats), deduped and
    sorted by ``updated_at`` descending."""
    if not chats_dir.exists():
        return []
    records: list[T] = []
    seen_ids: set[str] = set()

    # New format: {chat_id}/chat.json
    for cj in chats_dir.glob("*/chat.json"):
        try:
            rec = record_cls.model_validate_json(cj.read_text(encoding="utf-8"))
            seen_ids.add(rec.chat_id)  # type: ignore[attr-defined]
            records.append(rec)
        except Exception:
            continue

    # Old format: {chat_id}.json
    for p in chats_dir.glob("*.json"):
        try:
            rec = record_cls.model_validate_json(p.read_text(encoding="utf-8"))
            if rec.chat_id in seen_ids:  # type: ignore[attr-defined]
                continue
            seen_ids.add(rec.chat_id)  # type: ignore[attr-defined]
            records.append(rec)
        except Exception:
            continue

    records.sort(key=lambda r: r.updated_at, reverse=True)  # type: ignore[attr-defined]
    return records


def load_chat(chats_dir: Path, chat_id: str, record_cls: type[T]) -> T:
    """Load a single chat record by ID."""
    path = chat_path(chats_dir, chat_id)
    if not path.exists():
        raise FileNotFoundError(f"Chat '{chat_id}' not found.")
    return record_cls.model_validate_json(path.read_text(encoding="utf-8"))


def update_chat_fields(
    chats_dir: Path,
    chat_id: str,
    record_cls: type[T],
    *,
    mode: ChatMode | None = None,
    first_agent: AgentName | None = None,
    title: str | None = None,
) -> T:
    """Load a chat, patch the given fields, save, and return the updated record."""
    record = load_chat(chats_dir, chat_id, record_cls)
    if mode is not None:
        record.mode = mode  # type: ignore[attr-defined]
    if first_agent is not None:
        record.first_agent = first_agent  # type: ignore[attr-defined]
    if title is not None:
        record.title = title  # type: ignore[attr-defined]
    record.updated_at = datetime.now(UTC).replace(microsecond=0).isoformat()  # type: ignore[attr-defined]
    save_chat(chats_dir, record)
    return record


def delete_chat_dir(chats_dir: Path, chat_id: str) -> None:
    """Remove a chat's directory (new format) and/or legacy flat file."""
    removed = False
    d = chats_dir / chat_id
    if d.exists() and d.is_dir():
        shutil.rmtree(d)
        removed = True
    old = chats_dir / f"{chat_id}.json"
    if old.exists():
        old.unlink()
        removed = True
    # Also clean up old attachments dir (general chat legacy)
    old_att = chats_dir / f"{chat_id}_attachments"
    if old_att.exists():
        shutil.rmtree(old_att)


def append_message(
    chats_dir: Path,
    chat_id: str,
    record_cls: type[T],
    message_cls: type[BaseModel],
    *,
    role: str,
    content: str,
    agent: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> BaseModel:
    """Append a new message to a chat and return the message object.

    *extra_fields* is forwarded to the message constructor (e.g.
    ``attachments``, ``artifacts``).
    """
    record = load_chat(chats_dir, chat_id, record_cls)
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    kwargs: dict[str, Any] = {
        "id": f"msg_{uuid.uuid4().hex[:8]}",
        "role": role,
        "content": content,
        "agent": agent,
        "created_at": now,
    }
    if extra_fields:
        kwargs.update(extra_fields)
    msg = message_cls(**kwargs)
    record.messages.append(msg)  # type: ignore[attr-defined]
    record.updated_at = now  # type: ignore[attr-defined]
    save_chat(chats_dir, record)
    return msg


def find_artifact(messages: list[Any], artifact_id: str) -> Any | None:
    """Search all messages for an artifact with the given ID."""
    for msg in messages:
        for art in msg.artifacts:
            if art.id == artifact_id:
                return art
    return None
