from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TodoItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    title: str = ""
    body: str = ""
    status: str = "open"  # open | done | cancelled
    priority: str = "normal"  # low | normal | high
    labels: list[str] = Field(default_factory=list)
    source: str = "manual"  # manual | chat
    related_paper_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""


class DailyItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    todo_id: str = ""  # reference to backlog item, empty if ad-hoc
    title: str = ""  # used when todo_id is empty (ad-hoc daily item)
    status: str = "open"  # open | done
    priority: str = "normal"  # low | normal | high


class DailyTodos(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str = ""
    items: list[DailyItem] = Field(default_factory=list)
