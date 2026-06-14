"""Chat API routes: CRUD + SSE streaming via agent subprocess.

Agent tasks run in background threads, decoupled from SSE connections.
Navigating away no longer kills the agent — the task keeps running and
messages are saved when the agent finishes.  The frontend can reconnect
to an in-progress task via GET .../active-task/stream.
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from labit.api.agent_runtime import (
    CODEX_MAX_HISTORY,
    BackgroundTask,
    OrchestrationConfig,
    TaskRegistry,
    orchestrate,
    save_agent_result,
    stream_from_task,
)
from labit.api.downloads import attachment_content_disposition
from labit.api.chat_models import (
    AskRequest,
    ChatListItem,
    ChatMode,
    ChatRecord,
    CreateChatRequest,
    UpdateChatRequest,
)
from labit.api.chat_service import SYSTEM_PROMPT, ChatService

logger = logging.getLogger(__name__)

router = APIRouter()

_chat_service: ChatService | None = None
_task_registry = TaskRegistry()


def mount_chat_routes(chat_service: ChatService) -> APIRouter:
    global _chat_service
    _chat_service = chat_service
    return router


def _svc() -> ChatService:
    assert _chat_service is not None
    return _chat_service


def _task_key(project: str, paper_id: str, chat_id: str) -> str:
    return f"{project}/{paper_id}/{chat_id}"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project}/papers/{paper_id}/chats", response_model=ChatRecord)
def create_chat(project: str, paper_id: str, body: CreateChatRequest) -> ChatRecord:
    try:
        return _svc().create_chat(
            project, paper_id,
            title=body.title,
            mode=body.mode,
            first_agent=body.first_agent,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/papers/{paper_id}/chats", response_model=list[ChatListItem])
def list_chats(project: str, paper_id: str) -> list[ChatListItem]:
    try:
        return _svc().list_chats(project, paper_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}", response_model=ChatRecord)
def get_chat(project: str, paper_id: str, chat_id: str) -> ChatRecord:
    try:
        return _svc().get_chat(project, paper_id, chat_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}", response_model=ChatRecord)
def update_chat(project: str, paper_id: str, chat_id: str, body: UpdateChatRequest) -> ChatRecord:
    try:
        return _svc().update_chat(
            project, paper_id, chat_id,
            mode=body.mode,
            first_agent=body.first_agent,
            title=body.title,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}")
def delete_chat(project: str, paper_id: str, chat_id: str) -> dict:
    _svc().delete_chat(project, paper_id, chat_id)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

@router.get("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}/artifacts/{artifact_id}/download")
def download_artifact(project: str, paper_id: str, chat_id: str, artifact_id: str):
    svc = _svc()
    try:
        art = svc.get_artifact(project, paper_id, chat_id, artifact_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Chat not found") from exc
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")
    safe_mime = art.mime_type if art.mime_type != "text/html" else "text/plain"
    return StreamingResponse(
        iter([art.content.encode("utf-8")]),
        media_type=safe_mime,
        headers={
            "Content-Disposition": attachment_content_disposition(art.filename),
        },
    )


# ---------------------------------------------------------------------------
# Orchestration helpers
# ---------------------------------------------------------------------------

def _load_compute_profiles(svc: ChatService, project: str):
    try:
        ps = svc.paper_service.project_service
        resolved = ps.resolve_project_name(project)
        if resolved:
            spec = ps.load_project(resolved)
            return spec.compute_profiles
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# SSE endpoints
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}/ask")
async def ask(project: str, paper_id: str, chat_id: str, body: AskRequest):
    svc = _svc()
    try:
        chat = svc.get_chat(project, paper_id, chat_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    svc.append_message(project, paper_id, chat_id, "user", body.content)

    mode = chat.mode
    first = chat.first_agent
    second = "codex" if first == "claude" else "claude"
    agents = [first] if mode == ChatMode.SINGLE else [first, second]

    task = BackgroundTask()
    key = _task_key(project, paper_id, chat_id)
    _task_registry.set(key, task)

    project_cwd = svc.get_project_dir(project)
    config = OrchestrationConfig(
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["Read", "Grep", "Glob", "WebSearch", "WebFetch"],
        cwd=project_cwd,
        project=project,
        compute_profiles=_load_compute_profiles(svc, project),
    )

    def build_prompt(agent: str) -> str:
        max_history = CODEX_MAX_HISTORY if agent == "codex" else None
        return svc.build_prompt(project, paper_id, chat_id, max_history=max_history)

    def do_save_result(agent: str) -> None:
        save_agent_result(
            task, agent,
            chat_dir=svc.chat_dir(project, paper_id, chat_id),
            append_message_fn=lambda role, content, **kw: svc.append_message(project, paper_id, chat_id, role, content, **kw),
        )

    threading.Thread(
        target=orchestrate,
        args=(mode, agents, task, _task_registry, key, config),
        kwargs={"build_prompt": build_prompt, "save_result": do_save_result},
        daemon=True,
    ).start()

    return StreamingResponse(stream_from_task(task), media_type="text/event-stream")


@router.get("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}/active-task")
def get_active_task(project: str, paper_id: str, chat_id: str):
    task = _task_registry.get(_task_key(project, paper_id, chat_id))
    if not task or task.done:
        return {"active": False}
    events, _done = task.snapshot()
    return {"active": True, "event_count": len(events)}


@router.get("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}/active-task/stream")
async def stream_active_task(project: str, paper_id: str, chat_id: str):
    task = _task_registry.get(_task_key(project, paper_id, chat_id))
    if not task or task.done:
        raise HTTPException(status_code=404, detail="No active task")
    return StreamingResponse(stream_from_task(task), media_type="text/event-stream")


@router.post("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}/stop")
def stop_task(project: str, paper_id: str, chat_id: str):
    task = _task_registry.get(_task_key(project, paper_id, chat_id))
    if task and not task.done:
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}
