"""Code API routes: file browsing, content read/write, code-scoped chat with SSE."""
from __future__ import annotations

import logging
import mimetypes
import threading

from fastapi import APIRouter, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

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
from labit.api.downloads import attachment_content_disposition
from labit.api.code_models import (
    CodeFileContent,
    CodeFileRecord,
    CodeTreeEntry,
)
from labit.api.attachment_utils import ALLOWED_MIME_TYPES, MAX_UPLOAD_BYTES
from labit.api.code_service import (
    CODE_SYSTEM_PROMPT,
    CodeService,
    decode_file_id,
)

logger = logging.getLogger(__name__)

router = APIRouter()
_code_service: CodeService | None = None
_task_registry = TaskRegistry()


def mount_code_routes(code_service: CodeService) -> APIRouter:
    global _code_service
    _code_service = code_service
    return router


def _svc() -> CodeService:
    assert _code_service is not None
    return _code_service


def _task_key(project: str, chat_id: str) -> str:
    return f"code/{project}/{chat_id}"


# ---------------------------------------------------------------------------
# File browsing
# ---------------------------------------------------------------------------

@router.get("/api/projects/{project}/code/tree", response_model=list[CodeTreeEntry])
def get_tree(project: str, path: str = "") -> list[CodeTreeEntry]:
    try:
        return _svc().get_tree(project, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/projects/{project}/code/files/{file_id}", response_model=CodeFileRecord)
def get_file(project: str, file_id: str) -> CodeFileRecord:
    try:
        return _svc().get_file(project, file_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/code/files/{file_id}/content", response_model=CodeFileContent)
def get_file_content(project: str, file_id: str) -> CodeFileContent:
    try:
        content = _svc().get_content(project, file_id)
        return CodeFileContent(content=content)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/code/files/{file_id}/preview.pdf")
def preview_code_file_pdf(project: str, file_id: str) -> Response:
    """Render a markdown code file as PDF via weasyprint."""
    try:
        content = _svc().get_content(project, file_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        decoded_path = decode_file_id(file_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not decoded_path.lower().endswith((".md", ".mdx", ".markdown")):
        raise HTTPException(status_code=400, detail="PDF preview only supports markdown files")

    from markdown_it import MarkdownIt
    from mdit_py_plugins.dollarmath import dollarmath_plugin

    md = MarkdownIt().enable("table").enable("strikethrough")
    dollarmath_plugin(md)
    body_html = md.render(content)

    full_html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<style>"
        "body { font-family: sans-serif; max-width: 700px; margin: 40px auto;"
        " font-size: 14px; line-height: 1.6; color: #1e293b; }"
        "h1 { font-size: 24px; border-bottom: 1px solid #e2e8f0; padding-bottom: 8px; }"
        "h2 { font-size: 20px; } h3 { font-size: 16px; }"
        "table { border-collapse: collapse; width: 100%; margin: 16px 0; }"
        "th, td { border: 1px solid #cbd5e1; padding: 6px 12px; text-align: left; }"
        "th { background: #f1f5f9; font-weight: 600; }"
        "code { background: #f1f5f9; padding: 2px 4px; border-radius: 3px; font-size: 13px; }"
        "pre { background: #f1f5f9; padding: 12px; border-radius: 4px; overflow-x: auto; }"
        "pre code { background: none; padding: 0; }"
        "blockquote { border-left: 3px solid #94a3b8; margin: 16px 0; padding: 8px 16px;"
        " color: #475569; background: #f8fafc; }"
        "img { max-width: 100%; }"
        "hr { border: none; border-top: 1px solid #e2e8f0; margin: 24px 0; }"
        ".math { font-family: serif; font-style: italic; text-align: center;"
        " margin: 16px 0; font-size: 16px; }"
        "</style></head><body>"
        f"{body_html}"
        "</body></html>"
    )

    import weasyprint
    pdf_bytes = weasyprint.HTML(string=full_html).write_pdf()

    filename = decoded_path.rsplit("/", 1)[-1] if "/" in decoded_path else decoded_path
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}.pdf"'},
    )


@router.put("/api/projects/{project}/code/files/{file_id}/content", response_model=CodeFileRecord)
def save_file_content(project: str, file_id: str, body: CodeFileContent) -> CodeFileRecord:
    try:
        return _svc().save_content(project, file_id, body.content)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Apply artifact to file
# ---------------------------------------------------------------------------

class ApplyArtifactRequest(BaseModel):
    chat_id: str
    artifact_id: str


@router.post("/api/projects/{project}/code/files/{file_id}/apply-artifact", response_model=CodeFileRecord)
def apply_artifact(project: str, file_id: str, body: ApplyArtifactRequest) -> CodeFileRecord:
    try:
        return _svc().apply_artifact(project, file_id, body.chat_id, body.artifact_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Code Chat CRUD
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project}/code/chats", response_model=ChatRecord)
def create_code_chat(project: str, body: CreateChatRequest) -> ChatRecord:
    try:
        return _svc().create_chat(
            project, file_path=body.file_path,
            title=body.title, mode=body.mode, first_agent=body.first_agent,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/code/chats", response_model=list[ChatListItem])
def list_code_chats(project: str, file_path: str | None = None) -> list[ChatListItem]:
    return _svc().list_chats(project, file_path=file_path)


@router.get("/api/projects/{project}/code/chats/{chat_id}", response_model=ChatRecord)
def get_code_chat(project: str, chat_id: str) -> ChatRecord:
    try:
        return _svc().get_chat(project, chat_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/projects/{project}/code/chats/{chat_id}", response_model=ChatRecord)
def update_code_chat(project: str, chat_id: str, body: UpdateChatRequest) -> ChatRecord:
    try:
        return _svc().update_chat(project, chat_id, mode=body.mode, first_agent=body.first_agent, title=body.title)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/projects/{project}/code/chats/{chat_id}")
def delete_code_chat(project: str, chat_id: str) -> dict:
    try:
        _svc().delete_chat(project, chat_id)
        return {"deleted": True}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Artifact download
# ---------------------------------------------------------------------------

@router.get("/api/projects/{project}/code/chats/{chat_id}/artifacts/{artifact_id}/download")
def download_code_artifact(project: str, chat_id: str, artifact_id: str):
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
        headers={"Content-Disposition": attachment_content_disposition(art.filename)},
    )


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

@router.post(
    "/api/projects/{project}/code/chats/{chat_id}/attachments",
    response_model=Attachment,
)
async def upload_attachment(project: str, chat_id: str, file: UploadFile) -> Attachment:
    svc = _svc()
    try:
        svc.get_chat(project, chat_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {mime}. Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"File too large ({len(data)} bytes). Max: {MAX_UPLOAD_BYTES} bytes.")

    return svc.upload_attachment(project, chat_id, filename=file.filename or "image.png", mime_type=mime, data=data)


@router.get("/api/projects/{project}/code/chats/{chat_id}/attachments/{att_id}")
def get_attachment(project: str, chat_id: str, att_id: str):
    svc = _svc()
    att_id_clean = att_id.rsplit(".", 1)[0] if "." in att_id else att_id
    path = svc.get_attachment_path(project, chat_id, att_id_clean)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Attachment not found")
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=mime)


# ---------------------------------------------------------------------------
# Orchestration helpers
# ---------------------------------------------------------------------------

def _load_compute_profiles(svc: CodeService, project: str):
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

@router.post("/api/projects/{project}/code/chats/{chat_id}/ask")
async def code_ask(project: str, chat_id: str, body: AskRequest):
    svc = _svc()
    try:
        chat = svc.get_chat(project, chat_id)
    except (FileNotFoundError, ValueError) as exc:
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

    code_cwd = svc.get_project_dir(project)
    config = OrchestrationConfig(
        system_prompt=CODE_SYSTEM_PROMPT,
        allowed_tools=["Read", "Grep", "Glob", "Edit", "Write", "Bash", "WebSearch", "WebFetch"],
        cwd=code_cwd,
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
            chat_dir=svc.code_chat_dir(project, chat_id),
            append_message_fn=lambda role, content, **kw: svc.append_message(project, chat_id, role, content, **kw),
        )

    threading.Thread(
        target=orchestrate,
        args=(mode, agents, task, _task_registry, key, config),
        kwargs={"build_prompt": build_prompt, "save_result": do_save_result},
        daemon=True,
    ).start()

    return StreamingResponse(stream_from_task(task), media_type="text/event-stream")


@router.get("/api/projects/{project}/code/chats/{chat_id}/active-task")
def get_active_task(project: str, chat_id: str):
    task = _task_registry.get(_task_key(project, chat_id))
    if not task or task.done:
        return {"active": False}
    events, _ = task.snapshot()
    return {"active": True, "event_count": len(events)}


@router.get("/api/projects/{project}/code/chats/{chat_id}/active-task/stream")
async def stream_active_task(project: str, chat_id: str):
    task = _task_registry.get(_task_key(project, chat_id))
    if not task or task.done:
        raise HTTPException(status_code=404, detail="No active task")
    return StreamingResponse(stream_from_task(task), media_type="text/event-stream")


@router.post("/api/projects/{project}/code/chats/{chat_id}/stop")
def stop_task(project: str, chat_id: str):
    task = _task_registry.get(_task_key(project, chat_id))
    if task and not task.done:
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}
