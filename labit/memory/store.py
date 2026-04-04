from __future__ import annotations
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml

from labit.chat.models import utc_now_iso
from labit.memory.models import MemoryRecord, MemoryStatus
from labit.paths import RepoPaths


class MemoryStore:
    def __init__(self, paths: RepoPaths):
        self.paths = paths

    def memory_dir(self, project: str) -> Path:
        return self.paths.vault_projects_dir / project / "memory"

    def entries_dir(self, project: str) -> Path:
        return self.memory_dir(project) / "entries"

    def index_path(self, project: str) -> Path:
        return self.memory_dir(project) / "index.yaml"

    def entry_path(self, project: str, memory_id: str) -> Path:
        return self.entries_dir(project) / f"{memory_id}.yaml"

    def write_record(self, record: MemoryRecord) -> Path:
        path = self.entry_path(record.project, record.memory_id)
        self._write_yaml(path, record.model_dump(mode="json"))
        self._refresh_index(record.project)
        return path

    def load_record(self, project: str, memory_id: str) -> MemoryRecord:
        path = self.entry_path(project, memory_id)
        if not path.exists():
            raise FileNotFoundError(f"Memory '{memory_id}' not found for project '{project}'.")
        return MemoryRecord.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def delete_record(self, project: str, memory_id: str) -> Path:
        path = self.entry_path(project, memory_id)
        if not path.exists():
            raise FileNotFoundError(f"Memory '{memory_id}' not found for project '{project}'.")
        path.unlink()
        self._refresh_index(project)
        return path

    def archive_record(self, project: str, memory_id: str) -> MemoryRecord:
        record = self.load_record(project, memory_id)
        updated = record.model_copy(update={"status": MemoryStatus.ARCHIVED, "updated_at": utc_now_iso()})
        self.write_record(updated)
        return updated

    def supersede_record(self, project: str, memory_id: str, *, superseded_by: str) -> MemoryRecord:
        record = self.load_record(project, memory_id)
        updated = record.model_copy(
            update={
                "status": MemoryStatus.SUPERSEDED,
                "superseded_by": superseded_by,
                "superseded_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            }
        )
        self.write_record(updated)
        return updated

    def list_records(self, project: str, *, include_inactive: bool = False) -> list[MemoryRecord]:
        entries_dir = self.entries_dir(project)
        if not entries_dir.exists():
            return []
        records: list[MemoryRecord] = []
        for path in sorted(entries_dir.glob("*.yaml")):
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                record = MemoryRecord.model_validate(payload)
                if not include_inactive and record.status != MemoryStatus.ACTIVE:
                    continue
                records.append(record)
            except Exception:
                continue
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def _refresh_index(self, project: str) -> None:
        payload = [
            {
                "memory_id": record.memory_id,
                "kind": record.kind.value,
                "memory_type": record.memory_type.value,
                "title": record.title,
                "namespace": record.namespace.render(),
                "confidence": record.confidence,
                "status": record.status.value,
                "promotion_score": record.promotion_score,
                "updated_at": record.updated_at,
                "path": str(self.entry_path(project, record.memory_id).relative_to(self.paths.root)),
            }
            for record in self.list_records(project, include_inactive=True)
        ]
        self._write_yaml(self.index_path(project), payload)

    def _write_yaml(self, path: Path, payload: dict | list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        temp_path.replace(path)
