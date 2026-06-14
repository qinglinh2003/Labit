"""Doc API routes: document CRUD + doc-scoped chat with SSE streaming."""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
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
    ChatListItem,
    ChatMode,
    ChatRecord,
    CreateChatRequest,
    UpdateChatRequest,
)
from labit.api.downloads import attachment_content_disposition
from labit.api.doc_models import (
    CreateDocRequest,
    DocContent,
    DocRecord,
)
from labit.api.doc_service import DOC_SYSTEM_PROMPT, DocService

logger = logging.getLogger(__name__)

router = APIRouter()
_doc_service: DocService | None = None
_task_registry = TaskRegistry()


def mount_doc_routes(doc_service: DocService) -> APIRouter:
    global _doc_service
    _doc_service = doc_service
    return router


def _svc() -> DocService:
    assert _doc_service is not None
    return _doc_service


def _task_key(project: str, doc_id: str, chat_id: str) -> str:
    return f"doc/{project}/{doc_id}/{chat_id}"


# ---------------------------------------------------------------------------
# Document CRUD
# ---------------------------------------------------------------------------

@router.get("/api/projects/{project}/docs", response_model=list[DocRecord])
def list_docs(project: str) -> list[DocRecord]:
    return _svc().list_docs(project)


@router.get("/api/projects/{project}/docs/{doc_id}", response_model=DocRecord)
def get_doc(project: str, doc_id: str) -> DocRecord:
    try:
        return _svc().get_doc(project, doc_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/docs/{doc_id}/content", response_model=DocContent)
def get_doc_content(project: str, doc_id: str) -> DocContent:
    try:
        content = _svc().get_content(project, doc_id)
        return DocContent(content=content)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/docs/{doc_id}/preview.pdf")
def preview_doc_pdf(project: str, doc_id: str) -> Response:
    """Render a markdown document as PDF via weasyprint."""
    try:
        doc = _svc().get_doc(project, doc_id)
        content = _svc().get_content(project, doc_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if doc.format != "markdown":
        raise HTTPException(status_code=400, detail="PDF preview only supports markdown documents")

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

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{doc.filename}.pdf"'},
    )


@router.put("/api/projects/{project}/docs/{doc_id}/content", response_model=DocRecord)
def save_doc_content(project: str, doc_id: str, body: DocContent) -> DocRecord:
    try:
        return _svc().save_content(project, doc_id, body.content)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/projects/{project}/docs", response_model=DocRecord)
def create_doc(project: str, body: CreateDocRequest) -> DocRecord:
    try:
        return _svc().create_doc(project, body.filename, body.content)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/projects/{project}/docs/{doc_id}")
def delete_doc(project: str, doc_id: str) -> dict:
    try:
        _svc().delete_doc(project, doc_id)
        return {"deleted": True}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Apply artifact to doc
# ---------------------------------------------------------------------------

class ApplyArtifactRequest(BaseModel):
    chat_id: str
    artifact_id: str


@router.post("/api/projects/{project}/docs/{doc_id}/apply-artifact", response_model=DocRecord)
def apply_artifact(project: str, doc_id: str, body: ApplyArtifactRequest) -> DocRecord:
    try:
        return _svc().apply_artifact(project, doc_id, body.artifact_id, body.chat_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Doc Chat CRUD
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project}/docs/{doc_id}/chats", response_model=ChatRecord)
def create_doc_chat(project: str, doc_id: str, body: CreateChatRequest) -> ChatRecord:
    try:
        return _svc().create_chat(project, doc_id, title=body.title, mode=body.mode, first_agent=body.first_agent)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/docs/{doc_id}/chats", response_model=list[ChatListItem])
def list_doc_chats(project: str, doc_id: str) -> list[ChatListItem]:
    try:
        return _svc().list_chats(project, doc_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}", response_model=ChatRecord)
def get_doc_chat(project: str, doc_id: str, chat_id: str) -> ChatRecord:
    try:
        return _svc().get_chat(project, doc_id, chat_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}", response_model=ChatRecord)
def update_doc_chat(project: str, doc_id: str, chat_id: str, body: UpdateChatRequest) -> ChatRecord:
    try:
        return _svc().update_chat(project, doc_id, chat_id, mode=body.mode, first_agent=body.first_agent, title=body.title)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}")
def delete_doc_chat(project: str, doc_id: str, chat_id: str) -> dict:
    try:
        _svc().delete_chat(project, doc_id, chat_id)
        return {"deleted": True}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Artifact download
# ---------------------------------------------------------------------------

@router.get("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}/artifacts/{artifact_id}/download")
def download_doc_artifact(project: str, doc_id: str, chat_id: str, artifact_id: str):
    svc = _svc()
    try:
        art = svc.get_artifact(project, doc_id, chat_id, artifact_id)
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
# Orchestration helpers
# ---------------------------------------------------------------------------

def _load_compute_profiles(svc: DocService, project: str):
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

@router.post("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}/ask")
async def doc_ask(project: str, doc_id: str, chat_id: str, body: AskRequest):
    svc = _svc()
    try:
        chat = svc.get_chat(project, doc_id, chat_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    svc.append_message(project, doc_id, chat_id, "user", body.content)

    mode = chat.mode
    first = chat.first_agent
    second = "codex" if first == "claude" else "claude"
    agents = [first] if mode == ChatMode.SINGLE else [first, second]

    task = BackgroundTask()
    key = _task_key(project, doc_id, chat_id)
    _task_registry.set(key, task)

    project_cwd = svc.get_project_dir(project)
    config = OrchestrationConfig(
        system_prompt=DOC_SYSTEM_PROMPT,
        allowed_tools=["Read", "Grep", "Glob", "WebSearch", "WebFetch"],
        cwd=project_cwd,
        project=project,
        compute_profiles=_load_compute_profiles(svc, project),
    )

    def build_prompt(agent: str) -> str:
        max_history = CODEX_MAX_HISTORY if agent == "codex" else None
        return svc.build_prompt(project, doc_id, chat_id, max_history=max_history)

    def do_save_result(agent: str) -> None:
        save_agent_result(
            task, agent,
            chat_dir=svc.doc_chat_dir(project, doc_id, chat_id),
            append_message_fn=lambda role, content, **kw: svc.append_message(project, doc_id, chat_id, role, content, **kw),
        )

    threading.Thread(
        target=orchestrate,
        args=(mode, agents, task, _task_registry, key, config),
        kwargs={"build_prompt": build_prompt, "save_result": do_save_result},
        daemon=True,
    ).start()

    return StreamingResponse(stream_from_task(task), media_type="text/event-stream")


@router.get("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}/active-task")
def get_active_task(project: str, doc_id: str, chat_id: str):
    task = _task_registry.get(_task_key(project, doc_id, chat_id))
    if not task or task.done:
        return {"active": False}
    events, _ = task.snapshot()
    return {"active": True, "event_count": len(events)}


@router.get("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}/active-task/stream")
async def stream_active_task(project: str, doc_id: str, chat_id: str):
    task = _task_registry.get(_task_key(project, doc_id, chat_id))
    if not task or task.done:
        raise HTTPException(status_code=404, detail="No active task")
    return StreamingResponse(stream_from_task(task), media_type="text/event-stream")


@router.post("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}/stop")
def stop_task(project: str, doc_id: str, chat_id: str):
    task = _task_registry.get(_task_key(project, doc_id, chat_id))
    if task and not task.done:
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}
