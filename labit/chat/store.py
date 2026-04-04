from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from labit.chat.models import ChatMessage, ChatSession, ContextSnapshot
from labit.paths import RepoPaths


class ChatStore:
    def __init__(self, paths: RepoPaths):
        self.paths = paths

    def session_dir(self, session_id: str) -> Path:
        return self.paths.conversations_dir / session_id

    def attachments_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "attachments"

    def initialize_session(self, session: ChatSession, snapshot: ContextSnapshot) -> Path:
        session_dir = self.session_dir(session.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        self.write_session(session)
        self.write_context_snapshot(session.session_id, snapshot)
        transcript_path = session_dir / "transcript.jsonl"
        transcript_path.touch(exist_ok=True)
        events_path = session_dir / "events.jsonl"
        events_path.touch(exist_ok=True)
        self.attachments_dir(session.session_id).mkdir(parents=True, exist_ok=True)
        return session_dir

    def write_session(self, session: ChatSession) -> Path:
        path = self.session_dir(session.session_id) / "session.json"
        self._write_json(path, session.model_dump(mode="json"))
        return path

    def load_session(self, session_id: str) -> ChatSession:
        path = self.session_dir(session_id) / "session.json"
        if not path.exists():
            raise FileNotFoundError(f"Chat session '{session_id}' not found.")
        return ChatSession.model_validate(json.loads(path.read_text()))

    def write_context_snapshot(self, session_id: str, snapshot: ContextSnapshot) -> Path:
        path = self.session_dir(session_id) / "context.json"
        self._write_json(path, snapshot.model_dump(mode="json"))
        return path

    def load_context_snapshot(self, session_id: str) -> ContextSnapshot:
        path = self.session_dir(session_id) / "context.json"
        if not path.exists():
            return ContextSnapshot()
        return ContextSnapshot.model_validate(json.loads(path.read_text()))

    def append_message(self, message: ChatMessage) -> Path:
        path = self.session_dir(message.session_id) / "transcript.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message.model_dump(mode="json"), sort_keys=True))
            handle.write("\n")
        return path

    def load_transcript(self, session_id: str) -> list[ChatMessage]:
        path = self.session_dir(session_id) / "transcript.jsonl"
        if not path.exists():
            return []
        messages: list[ChatMessage] = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            messages.append(ChatMessage.model_validate(json.loads(line)))
        return messages

    def list_sessions(self) -> list[ChatSession]:
        if not self.paths.conversations_dir.exists():
            return []
        sessions: list[ChatSession] = []
        for path in sorted(self.paths.conversations_dir.glob("*/session.json")):
            try:
                sessions.append(ChatSession.model_validate(json.loads(path.read_text())))
            except Exception:
                continue
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=2, sort_keys=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        temp_path.replace(path)
