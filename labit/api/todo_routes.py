from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from labit.todos.models import DailyItem, DailyTodos, TodoItem
from labit.todos.service import TodoService


class CreateTodoRequest(BaseModel):
    title: str
    body: str = ""
    priority: str = "normal"
    labels: list[str] = Field(default_factory=list)
    related_paper_id: str = ""


class UpdateTodoRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    status: str | None = None
    priority: str | None = None
    labels: list[str] | None = None
    related_paper_id: str | None = None


class AddDailyItemRequest(BaseModel):
    todo_id: str = ""
    title: str = ""
    priority: str | None = None


class UpdateDailyItemRequest(BaseModel):
    status: str | None = None
    title: str | None = None
    priority: str | None = None


class CarryOverRequest(BaseModel):
    from_date: str
    to_date: str


def mount_todo_routes(todo_service: TodoService) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project}/todos", tags=["todos"])

    # ── Backlog ──

    @router.get("", response_model=list[TodoItem])
    def list_todos(project: str, status: str = "", label: str = "") -> list[TodoItem]:
        try:
            return todo_service.list_todos(project, status=status, label=label)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("", response_model=TodoItem, status_code=201)
    def create_todo(project: str, body: CreateTodoRequest) -> TodoItem:
        try:
            return todo_service.create_todo(
                project,
                title=body.title,
                body=body.body,
                priority=body.priority,
                labels=body.labels,
                related_paper_id=body.related_paper_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/{todo_id}", response_model=TodoItem)
    def update_todo(project: str, todo_id: str, body: UpdateTodoRequest) -> TodoItem:
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if not fields:
            raise HTTPException(status_code=400, detail="No fields to update")
        try:
            return todo_service.update_todo(project, todo_id, **fields)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete("/{todo_id}", status_code=204)
    def delete_todo(project: str, todo_id: str) -> None:
        try:
            todo_service.delete_todo(project, todo_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ── Daily ──

    daily = APIRouter(prefix="/api/projects/{project}/daily/{date}", tags=["daily"])

    @daily.get("", response_model=DailyTodos)
    def get_daily(project: str, date: str) -> DailyTodos:
        try:
            return todo_service.get_daily(project, date)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @daily.post("/items", response_model=DailyItem, status_code=201)
    def add_daily_item(project: str, date: str, body: AddDailyItemRequest) -> DailyItem:
        try:
            return todo_service.add_daily_item(
                project, date, todo_id=body.todo_id, title=body.title, priority=body.priority,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @daily.patch("/items/{item_id}", response_model=DailyItem)
    def update_daily_item(project: str, date: str, item_id: str,
                          body: UpdateDailyItemRequest) -> DailyItem:
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if not fields:
            raise HTTPException(status_code=400, detail="No fields to update")
        try:
            return todo_service.update_daily_item(project, date, item_id, **fields)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @daily.delete("/items/{item_id}", status_code=204)
    def delete_daily_item(project: str, date: str, item_id: str) -> None:
        try:
            todo_service.delete_daily_item(project, date, item_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @daily.post("/carry-over", response_model=DailyTodos)
    def carry_over(project: str, date: str, body: CarryOverRequest) -> DailyTodos:
        try:
            return todo_service.carry_over(project, body.from_date, body.to_date)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return APIRouter(routes=router.routes + daily.routes)
