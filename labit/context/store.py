from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from labit.context.events import SessionEvent, WorkingMemorySnapshot
from labit.paths import RepoPaths


class SessionContextStore:
    def __init__(self, paths: RepoPaths):
        self.paths = paths

    def events_path(self, session_id: str) -> Path:
        return self.paths.conversations_dir / session_id / "events.jsonl"

    def working_memory_path(self, session_id: str) -> Path:
        return self.paths.conversations_dir / session_id / "working_memory.json"

    def append_event(self, event: SessionEvent) -> Path:
        path = self.events_path(event.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json"), sort_keys=True))
            handle.write("\n")
        return path

    def load_events(self, session_id: str) -> list[SessionEvent]:
        path = self.events_path(session_id)
        if not path.exists():
            return []
        events: list[SessionEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            events.append(SessionEvent.model_validate(json.loads(line)))
        return events

    def write_working_memory(self, snapshot: WorkingMemorySnapshot) -> Path:
        path = self.working_memory_path(snapshot.session_id)
        self._write_json(path, snapshot.model_dump(mode="json"))
        return path

    def load_working_memory(self, session_id: str) -> WorkingMemorySnapshot | None:
        path = self.working_memory_path(session_id)
        if not path.exists():
            return None
        return WorkingMemorySnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=2, sort_keys=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        temp_path.replace(path)
