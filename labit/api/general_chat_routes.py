"""General (project-scoped) chat API routes: CRUD + SSE streaming."""
from __future__ import annotations

import logging
import mimetypes
import threading

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from labit.api.agent_runtime import (
    CODEX_MAX_HISTORY,
    BackgroundTask,
    OrchestrationConfig,
    TaskRegistry,
    orchestrate,
    save_agent_result,
    stream_from_task,
)
from labit.api.chat_models import (
    AskRequest,
    Attachment,
    ChatListItem,
    ChatMode,
    ChatRecord,
    CreateChatRequest,
    UpdateChatRequest,
)
from labit.api.attachment_utils import ALLOWED_MIME_TYPES, MAX_UPLOAD_BYTES
from labit.api.general_chat_service import (
    SYSTEM_PROMPT,
    GeneralChatService,
)
from labit.api.downloads import attachment_content_disposition

logger = logging.getLogger(__name__)

router = APIRouter()

_service: GeneralChatService | None = None
_task_registry = TaskRegistry()


def mount_general_chat_routes(service: GeneralChatService) -> APIRouter:
    global _service
    _service = service
    return router


def _svc() -> GeneralChatService:
    assert _service is not None
    return _service


def _task_key(project: str, chat_id: str) -> str:
    return f"{project}/__general__/{chat_id}"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project}/chats", response_model=ChatRecord)
def create_chat(project: str, body: CreateChatRequest) -> ChatRecord:
    return _svc().create_chat(
        project,
        title=body.title,
        mode=body.mode,
        first_agent=body.first_agent,
    )


@router.get("/api/projects/{project}/chats", response_model=list[ChatListItem])
def list_chats(project: str) -> list[ChatListItem]:
    return _svc().list_chats(project)


@router.get("/api/projects/{project}/chats/{chat_id}", response_model=ChatRecord)
def get_chat(project: str, chat_id: str) -> ChatRecord:
    try:
        return _svc().get_chat(project, chat_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/projects/{project}/chats/{chat_id}", response_model=ChatRecord)
def update_chat(project: str, chat_id: str, body: UpdateChatRequest) -> ChatRecord:
    try:
        return _svc().update_chat(
            project, chat_id,
            mode=body.mode,
            first_agent=body.first_agent,
            title=body.title,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/projects/{project}/chats/{chat_id}")
def delete_chat(project: str, chat_id: str) -> dict:
    _svc().delete_chat(project, chat_id)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

@router.post(
    "/api/projects/{project}/chats/{chat_id}/attachments",
    response_model=Attachment,
)
async def upload_attachment(project: str, chat_id: str, file: UploadFile) -> Attachment:
    svc = _svc()
    try:
        svc.get_chat(project, chat_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {mime}. Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"File too large ({len(data)} bytes). Max: {MAX_UPLOAD_BYTES} bytes.")

    att = svc.upload_attachment(
        project, chat_id,
        filename=file.filename or "image.png",
        mime_type=mime,
        data=data,
    )
    return att


@router.get("/api/projects/{project}/chats/{chat_id}/attachments/{att_id}")
def get_attachment(project: str, chat_id: str, att_id: str):
    svc = _svc()
    att_id_clean = att_id.rsplit(".", 1)[0] if "." in att_id else att_id
    path = svc.get_attachment_path(project, chat_id, att_id_clean)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Attachment not found")
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=mime)


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

@router.get("/api/projects/{project}/chats/{chat_id}/artifacts/{artifact_id}/download")
def download_artifact(project: str, chat_id: str, artifact_id: str):
    svc = _svc()
    try:
        art = svc.get_artifact(project, chat_id, artifact_id)
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

def _load_compute_profiles(svc: GeneralChatService, project: str):
    try:
        ps = svc.project_service
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

@router.post("/api/projects/{project}/chats/{chat_id}/ask")
async def ask(project: str, chat_id: str, body: AskRequest):
    svc = _svc()
    try:
        chat = svc.get_chat(project, chat_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Resolve attachments
    attachments: list[Attachment] = []
    if body.attachment_ids:
        attachments = svc.resolve_attachment_ids(project, chat_id, body.attachment_ids)
        found_ids = {att.id for att in attachments}
        missing_ids = [att_id for att_id in body.attachment_ids if att_id not in found_ids]
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Attachment not found for chat: {', '.join(missing_ids)}",
            )

    svc.append_message(project, chat_id, "user", body.content, attachments=attachments)

    image_paths = [a.path for a in attachments if a.kind == "image"]

    mode = chat.mode
    first = chat.first_agent
    second = "codex" if first == "claude" else "claude"
    agents = [first] if mode == ChatMode.SINGLE else [first, second]

    task = BackgroundTask()
    key = _task_key(project, chat_id)
    _task_registry.set(key, task)

    project_cwd = svc.get_project_dir(project)
    config = OrchestrationConfig(
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["Read", "Grep", "Glob", "WebSearch", "WebFetch"],
        cwd=project_cwd,
        image_paths=image_paths,
        project=project,
        compute_profiles=_load_compute_profiles(svc, project),
    )

    def build_prompt(agent: str) -> str:
        max_history = CODEX_MAX_HISTORY if agent == "codex" else None
        return svc.build_prompt(project, chat_id, max_history=max_history)

    def do_save_result(agent: str) -> None:
        save_agent_result(
            task, agent,
            chat_dir=svc.chat_dir(project, chat_id),
            append_message_fn=lambda role, content, **kw: svc.append_message(project, chat_id, role, content, **kw),
        )

    threading.Thread(
        target=orchestrate,
        args=(mode, agents, task, _task_registry, key, config),
        kwargs={"build_prompt": build_prompt, "save_result": do_save_result},
        daemon=True,
    ).start()

    return StreamingResponse(stream_from_task(task), media_type="text/event-stream")


@router.get("/api/projects/{project}/chats/{chat_id}/active-task")
def get_active_task(project: str, chat_id: str):
    task = _task_registry.get(_task_key(project, chat_id))
    if not task or task.done:
        return {"active": False}
    events, _done = task.snapshot()
    return {"active": True, "event_count": len(events)}


@router.get("/api/projects/{project}/chats/{chat_id}/active-task/stream")
async def stream_active_task(project: str, chat_id: str):
    task = _task_registry.get(_task_key(project, chat_id))
    if not task or task.done:
        raise HTTPException(status_code=404, detail="No active task")
    return StreamingResponse(stream_from_task(task), media_type="text/event-stream")


@router.post("/api/projects/{project}/chats/{chat_id}/stop")
def stop_task(project: str, chat_id: str):
    task = _task_registry.get(_task_key(project, chat_id))
    if task and not task.done:
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}
