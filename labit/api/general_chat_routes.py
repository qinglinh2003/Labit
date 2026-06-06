"""General (project-scoped) chat API routes: CRUD + SSE streaming."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import threading
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from labit.agents.adapters.base import AgentAdapterError, StreamCancelled
from labit.agents.adapters.claude import ClaudeAdapter
from labit.agents.adapters.codex import CodexAdapter
from labit.agents.models import AgentRequest, AgentRole
from labit.api.chat_models import ChatMode
from labit.api.general_chat_models import (
    Attachment,
    CreateGeneralChatRequest,
    GeneralAskRequest,
    GeneralChatListItem,
    GeneralChatRecord,
    UpdateGeneralChatRequest,
)
from labit.api.general_chat_service import (
    ALLOWED_MIME_TYPES,
    MAX_UPLOAD_BYTES,
    SYSTEM_PROMPT,
    GeneralChatService,
    extract_artifacts,
)
from labit.api.artifact_storage import write_artifact_file
from labit.api.downloads import attachment_content_disposition

router = APIRouter()

_service: GeneralChatService | None = None


def mount_general_chat_routes(service: GeneralChatService) -> APIRouter:
    global _service
    _service = service
    return router


def _svc() -> GeneralChatService:
    assert _service is not None
    return _service


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project}/chats", response_model=GeneralChatRecord)
def create_chat(project: str, body: CreateGeneralChatRequest) -> GeneralChatRecord:
    return _svc().create_chat(
        project,
        title=body.title,
        mode=body.mode,
        first_agent=body.first_agent,
    )


@router.get("/api/projects/{project}/chats", response_model=list[GeneralChatListItem])
def list_chats(project: str) -> list[GeneralChatListItem]:
    return _svc().list_chats(project)


@router.get("/api/projects/{project}/chats/{chat_id}", response_model=GeneralChatRecord)
def get_chat(project: str, chat_id: str) -> GeneralChatRecord:
    try:
        return _svc().get_chat(project, chat_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/projects/{project}/chats/{chat_id}", response_model=GeneralChatRecord)
def update_chat(project: str, chat_id: str, body: UpdateGeneralChatRequest) -> GeneralChatRecord:
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
    # Validate chat exists
    try:
        svc.get_chat(project, chat_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Validate MIME type
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
    # Strip extension if present (e.g. "abc123.png" -> "abc123")
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
    # Force safe content types — never serve text/html directly
    safe_mime = art.mime_type if art.mime_type != "text/html" else "text/plain"
    return StreamingResponse(
        iter([art.content.encode("utf-8")]),
        media_type=safe_mime,
        headers={
            "Content-Disposition": attachment_content_disposition(art.filename),
        },
    )


# ---------------------------------------------------------------------------
# Background task registry (same pattern as paper chat)
# ---------------------------------------------------------------------------

@dataclass
class BackgroundTask:
    events: list[dict] = field(default_factory=list)
    cancel_events: list[threading.Event] = field(default_factory=list)
    done: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def push_event(self, event: dict) -> None:
        with self._lock:
            self.events.append(event)

    def mark_done(self) -> None:
        with self._lock:
            self.done = True

    def snapshot(self) -> tuple[list[dict], bool]:
        with self._lock:
            return list(self.events), self.done

    def cancel(self) -> None:
        for ce in self.cancel_events:
            ce.set()


_active_tasks: dict[str, BackgroundTask] = {}
_tasks_lock = threading.Lock()


def _chat_key(project: str, chat_id: str) -> str:
    return f"{project}/__general__/{chat_id}"


def _get_task(project: str, chat_id: str) -> BackgroundTask | None:
    with _tasks_lock:
        return _active_tasks.get(_chat_key(project, chat_id))


def _set_task(project: str, chat_id: str, task: BackgroundTask) -> None:
    key = _chat_key(project, chat_id)
    with _tasks_lock:
        old = _active_tasks.get(key)
        if old and not old.done:
            old.cancel()
        _active_tasks[key] = task


def _remove_task(project: str, chat_id: str) -> None:
    key = _chat_key(project, chat_id)
    with _tasks_lock:
        _active_tasks.pop(key, None)


# ---------------------------------------------------------------------------
# Agent execution
# ---------------------------------------------------------------------------

def _get_adapter(agent: str):
    if agent == "claude":
        return ClaudeAdapter()
    elif agent == "codex":
        return CodexAdapter()
    raise ValueError(f"Unknown agent: {agent}")


def _run_agent_to_task(
    agent: str,
    prompt: str,
    system_prompt: str,
    task: BackgroundTask,
    cancel_event: threading.Event,
    image_paths: list[str] | None = None,
    *,
    cwd: str | None = None,
) -> None:
    adapter = _get_adapter(agent)
    allowed_tools: list[str] = []
    if agent == "claude":
        allowed_tools = ["Read", "Grep", "Glob", "WebSearch", "WebFetch"]

    request = AgentRequest(
        role=AgentRole.DISCUSSANT,
        prompt=prompt,
        system_prompt=system_prompt,
        timeout_seconds=120,
        allowed_tools=allowed_tools,
        image_paths=image_paths or [],
        cwd=cwd,
    )

    collected: list[str] = []

    def on_text(chunk: str) -> None:
        collected.append(chunk)
        task.push_event({"type": "text", "agent": agent, "text": chunk})

    def on_status(status: str) -> None:
        task.push_event({"type": "status", "agent": agent, "status": status})

    try:
        task.push_event({"type": "start", "agent": agent})
        adapter.run_stream(request, on_text=on_text, on_status=on_status, cancel_event=cancel_event)
        task.push_event({"type": "done", "agent": agent, "full_text": "".join(collected)})
    except StreamCancelled:
        task.push_event({"type": "done", "agent": agent, "full_text": "".join(collected)})
    except AgentAdapterError as exc:
        partial = "".join(collected)
        task.push_event({"type": "error", "agent": agent, "error": str(exc), "full_text": partial})


def _get_agent_text(task: BackgroundTask, agent: str) -> str:
    events, _done = task.snapshot()
    for ev in events:
        if ev.get("agent") == agent and ev.get("type") in ("done", "error"):
            return ev.get("full_text", "")
    return ""


def _save_agent_result(
    svc: GeneralChatService, project: str, chat_id: str,
    agent: str, task: BackgroundTask,
) -> None:
    text = _get_agent_text(task, agent)
    if text:
        cleaned, artifacts = extract_artifacts(text, agent=agent)
        # Write artifact files to chat directory
        if artifacts:
            chat_dir = svc.chat_dir(project, chat_id)
            for art in artifacts:
                try:
                    write_artifact_file(chat_dir, art)
                except Exception:
                    pass  # file write failure shouldn't block message save
        svc.append_message(
            project, chat_id, "assistant", cleaned,
            agent=agent, artifacts=artifacts if artifacts else None,
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _orchestrate_single(
    svc: GeneralChatService, project: str, chat_id: str,
    agent: str, task: BackgroundTask, image_paths: list[str] | None = None,
) -> None:
    try:
        prompt = svc.build_prompt(project, chat_id)
        project_cwd = svc.get_project_dir(project)
        cancel_event = threading.Event()
        task.cancel_events.append(cancel_event)
        _run_agent_to_task(agent, prompt, SYSTEM_PROMPT, task, cancel_event, image_paths=image_paths, cwd=project_cwd)
        _save_agent_result(svc, project, chat_id, agent, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, chat_id)


def _orchestrate_parallel(
    svc: GeneralChatService, project: str, chat_id: str,
    agents: list[str], task: BackgroundTask, image_paths: list[str] | None = None,
) -> None:
    try:
        prompt = svc.build_prompt(project, chat_id)
        project_cwd = svc.get_project_dir(project)
        threads = []
        for agent in agents:
            cancel_event = threading.Event()
            task.cancel_events.append(cancel_event)
            t = threading.Thread(
                target=_run_agent_to_task,
                args=(agent, prompt, SYSTEM_PROMPT, task, cancel_event),
                kwargs={"image_paths": image_paths, "cwd": project_cwd},
                daemon=True,
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        for agent in agents:
            _save_agent_result(svc, project, chat_id, agent, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, chat_id)


def _orchestrate_round_robin(
    svc: GeneralChatService, project: str, chat_id: str,
    agents: list[str], task: BackgroundTask, image_paths: list[str] | None = None,
) -> None:
    try:
        first = agents[0]
        prompt = svc.build_prompt(project, chat_id)
        project_cwd = svc.get_project_dir(project)
        cancel_event = threading.Event()
        task.cancel_events.append(cancel_event)
        _run_agent_to_task(first, prompt, SYSTEM_PROMPT, task, cancel_event, image_paths=image_paths, cwd=project_cwd)
        _save_agent_result(svc, project, chat_id, first, task)

        first_text = _get_agent_text(task, first)
        if len(agents) > 1 and first_text:
            second = agents[1]
            prompt2 = svc.build_prompt(project, chat_id)
            cancel_event2 = threading.Event()
            task.cancel_events.append(cancel_event2)
            # Don't re-send images for the second agent in round robin
            _run_agent_to_task(second, prompt2, SYSTEM_PROMPT, task, cancel_event2, cwd=project_cwd)
            _save_agent_result(svc, project, chat_id, second, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, chat_id)


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------

async def _stream_from_task(task: BackgroundTask) -> AsyncGenerator[str, None]:
    cursor = 0
    while True:
        events, done = task.snapshot()
        while cursor < len(events):
            event = events[cursor]
            event_type = event.get("type", "unknown")
            yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
            cursor += 1
            if event_type == "end":
                return
        if done:
            return
        yield ": keepalive\n\n"
        await asyncio.sleep(0.5)


@router.post("/api/projects/{project}/chats/{chat_id}/ask")
async def ask(project: str, chat_id: str, body: GeneralAskRequest):
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

    # Collect image paths from the current message's attachments
    image_paths = [a.path for a in attachments if a.kind == "image"]

    mode = chat.mode
    first = chat.first_agent
    second = "codex" if first == "claude" else "claude"

    task = BackgroundTask()
    _set_task(project, chat_id, task)

    if mode == ChatMode.SINGLE:
        target = _orchestrate_single
        args = (svc, project, chat_id, first, task)
        kwargs = {"image_paths": image_paths}
    elif mode == ChatMode.PARALLEL:
        target = _orchestrate_parallel
        args = (svc, project, chat_id, [first, second], task)
        kwargs = {"image_paths": image_paths}
    else:
        target = _orchestrate_round_robin
        args = (svc, project, chat_id, [first, second], task)
        kwargs = {"image_paths": image_paths}

    threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True).start()

    return StreamingResponse(_stream_from_task(task), media_type="text/event-stream")


@router.get("/api/projects/{project}/chats/{chat_id}/active-task")
def get_active_task(project: str, chat_id: str):
    task = _get_task(project, chat_id)
    if not task or task.done:
        return {"active": False}
    events, _done = task.snapshot()
    return {"active": True, "event_count": len(events)}


@router.get("/api/projects/{project}/chats/{chat_id}/active-task/stream")
async def stream_active_task(project: str, chat_id: str):
    task = _get_task(project, chat_id)
    if not task or task.done:
        raise HTTPException(status_code=404, detail="No active task")
    return StreamingResponse(_stream_from_task(task), media_type="text/event-stream")


@router.post("/api/projects/{project}/chats/{chat_id}/stop")
def stop_task(project: str, chat_id: str):
    task = _get_task(project, chat_id)
    if task and not task.done:
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}
