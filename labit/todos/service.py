from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path

import yaml

from labit.paths import RepoPaths
from labit.services.project_service import ProjectService
from labit.todos.models import DailyItem, DailyTodos, TodoItem


class TodoService:
    def __init__(self, paths: RepoPaths, *, project_service: ProjectService | None = None):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)

    # ── helpers ──

    def _project_dir(self, project: str) -> Path:
        d = self.paths.vault_projects_dir / project
        if not d.exists():
            raise FileNotFoundError(f"Project not found: {project}")
        return d

    def _todos_path(self, project: str) -> Path:
        return self._project_dir(project) / "todos.yaml"

    def _daily_dir(self, project: str) -> Path:
        d = self._project_dir(project) / "daily"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _daily_path(self, project: str, date: str) -> Path:
        return self._daily_dir(project) / f"{date}.yaml"

    def _gen_id(self) -> str:
        return secrets.token_hex(3)

    def _now_iso(self) -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat()

    # ── backlog CRUD ──

    def _load_todos(self, project: str) -> list[TodoItem]:
        path = self._todos_path(project)
        if not path.exists():
            return []
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return [TodoItem.model_validate(item) for item in (raw.get("todos") or [])]

    def _save_todos(self, project: str, todos: list[TodoItem]) -> None:
        path = self._todos_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"todos": [item.model_dump() for item in todos]}
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def list_todos(self, project: str, *, status: str = "", label: str = "") -> list[TodoItem]:
        todos = self._load_todos(project)
        if status:
            todos = [t for t in todos if t.status == status]
        if label:
            todos = [t for t in todos if label in t.labels]
        return todos

    def get_todo(self, project: str, todo_id: str) -> TodoItem:
        for t in self._load_todos(project):
            if t.id == todo_id:
                return t
        raise FileNotFoundError(f"Todo not found: {todo_id}")

    def create_todo(self, project: str, *, title: str, body: str = "", priority: str = "normal",
                    labels: list[str] | None = None, related_paper_id: str = "") -> TodoItem:
        todos = self._load_todos(project)
        now = self._now_iso()
        item = TodoItem(
            id=self._gen_id(),
            title=title,
            body=body,
            priority=priority,
            labels=labels or [],
            related_paper_id=related_paper_id,
            created_at=now,
            updated_at=now,
        )
        todos.insert(0, item)
        self._save_todos(project, todos)
        return item

    def update_todo(self, project: str, todo_id: str, **fields: object) -> TodoItem:
        todos = self._load_todos(project)
        for i, t in enumerate(todos):
            if t.id == todo_id:
                data = t.model_dump()
                for k, v in fields.items():
                    if k in data:
                        data[k] = v
                data["updated_at"] = self._now_iso()
                if data.get("status") == "done" and not data.get("completed_at"):
                    data["completed_at"] = self._now_iso()
                elif data.get("status") != "done":
                    data["completed_at"] = ""
                updated = TodoItem.model_validate(data)
                todos[i] = updated
                self._save_todos(project, todos)
                return updated
        raise FileNotFoundError(f"Todo not found: {todo_id}")

    def delete_todo(self, project: str, todo_id: str) -> None:
        todos = self._load_todos(project)
        new = [t for t in todos if t.id != todo_id]
        if len(new) == len(todos):
            raise FileNotFoundError(f"Todo not found: {todo_id}")
        self._save_todos(project, new)

    # ── daily CRUD ──

    def _load_daily(self, project: str, date: str) -> DailyTodos:
        path = self._daily_path(project, date)
        if not path.exists():
            return DailyTodos(date=date)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return DailyTodos.model_validate(raw)

    def _save_daily(self, project: str, daily: DailyTodos) -> None:
        path = self._daily_path(project, daily.date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(daily.model_dump(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def get_daily(self, project: str, date: str) -> DailyTodos:
        daily = self._load_daily(project, date)
        # Resolve titles from backlog for referenced items.
        todos = {t.id: t for t in self._load_todos(project)}
        for item in daily.items:
            if not item.todo_id:
                continue
            if not item.title:
                ref = todos.get(item.todo_id)
                if ref:
                    item.title = ref.title
        return daily

    def add_daily_item(self, project: str, date: str, *, todo_id: str = "",
                       title: str = "", priority: str | None = None) -> DailyItem:
        daily = self._load_daily(project, date)
        item_priority = priority or "normal"
        # Don't add duplicate backlog references
        if todo_id:
            for item in daily.items:
                if item.todo_id == todo_id:
                    return item
            try:
                ref = self.get_todo(project, todo_id)
                title = title or ref.title
                item_priority = priority or ref.priority
            except FileNotFoundError:
                pass
        item = DailyItem(
            id=self._gen_id(),
            todo_id=todo_id,
            title=title,
            status="open",
            priority=item_priority,
        )
        daily.items.append(item)
        self._save_daily(project, daily)
        return item

    def update_daily_item(self, project: str, date: str, item_id: str,
                          **fields: object) -> DailyItem:
        daily = self._load_daily(project, date)
        for i, item in enumerate(daily.items):
            if item.id == item_id:
                data = item.model_dump()
                for k, v in fields.items():
                    if k in data:
                        data[k] = v
                updated = DailyItem.model_validate(data)
                daily.items[i] = updated
                self._save_daily(project, daily)
                # Sync status back to backlog if linked
                if updated.todo_id and "status" in fields:
                    backlog_status = "done" if updated.status == "done" else "open"
                    try:
                        self.update_todo(project, updated.todo_id, status=backlog_status)
                    except FileNotFoundError:
                        pass
                return updated
        raise FileNotFoundError(f"Daily item not found: {item_id}")

    def delete_daily_item(self, project: str, date: str, item_id: str) -> None:
        daily = self._load_daily(project, date)
        new_items = [item for item in daily.items if item.id != item_id]
        if len(new_items) == len(daily.items):
            raise FileNotFoundError(f"Daily item not found: {item_id}")
        daily.items = new_items
        self._save_daily(project, daily)

    def carry_over(self, project: str, from_date: str, to_date: str) -> DailyTodos:
        """Copy undone items from from_date to to_date."""
        source = self._load_daily(project, from_date)
        target = self._load_daily(project, to_date)
        existing_todo_ids = {item.todo_id for item in target.items if item.todo_id}
        for item in source.items:
            if item.status != "done":
                if item.todo_id and item.todo_id in existing_todo_ids:
                    continue
                new_item = DailyItem(
                    id=self._gen_id(),
                    todo_id=item.todo_id,
                    title=item.title,
                    status="open",
                    priority=item.priority,
                )
                target.items.append(new_item)
        self._save_daily(project, target)
        return self.get_daily(project, to_date)
