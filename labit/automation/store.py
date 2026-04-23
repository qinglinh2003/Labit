from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml

from labit.automation.models import AutoIterationEntry, AutoSessionRecord, utc_now_iso
from labit.paths import RepoPaths


class AutomationStore:
    def __init__(self, paths: RepoPaths):
        self.paths = paths

    def automation_dir(self, project: str) -> Path:
        return self.paths.vault_projects_dir / project / "automation"

    def session_path(self, project: str) -> Path:
        return self.automation_dir(project) / "session.yaml"

    def iterations_path(self, project: str) -> Path:
        return self.automation_dir(project) / "iterations.jsonl"

    def load_session(self, project: str) -> AutoSessionRecord | None:
        path = self.session_path(project)
        if not path.exists():
            return None
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return AutoSessionRecord.model_validate(payload)

    def save_session(self, session: AutoSessionRecord) -> AutoSessionRecord:
        updated = session.model_copy(update={"updated_at": utc_now_iso()})
        self._write_yaml(self.session_path(session.project), updated.model_dump(mode="json"))
        return updated

    def append_iteration(self, project: str, entry: AutoIterationEntry) -> Path:
        path = self.iterations_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return path

    def recent_iterations(self, project: str, limit: int = 5) -> list[AutoIterationEntry]:
        path = self.iterations_path(project)
        if not path.exists():
            return []
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        entries: list[AutoIterationEntry] = []
        for raw in lines[-limit:]:
            try:
                entries.append(AutoIterationEntry.model_validate(json.loads(raw)))
            except Exception:
                continue
        return entries

    def snapshot_path(self, project: str) -> Path:
        return self.automation_dir(project) / "latest.md"

    def save_snapshot(self, project: str, content: str) -> Path:
        path = self.snapshot_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_yaml(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        temp_path.replace(path)
