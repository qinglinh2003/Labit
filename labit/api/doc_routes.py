"""Doc API routes: document CRUD + doc-scoped chat with SSE streaming."""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from labit.api.artifact_storage import write_artifact_file
from labit.api.downloads import attachment_content_disposition
from labit.api.general_chat_service import extract_artifacts

from labit.agents.adapters.base import AgentAdapterError, StreamCancelled
from labit.agents.adapters.claude import ClaudeAdapter
from labit.agents.adapters.codex import CodexAdapter
from labit.agents.models import AgentRequest, AgentRole
from labit.api.doc_models import (
    CreateDocChatRequest,
    CreateDocRequest,
    DocAskRequest,
    DocChatListItem,
    DocChatRecord,
    DocContent,
    DocRecord,
    UpdateDocChatRequest,
)
from labit.api.doc_service import DOC_SYSTEM_PROMPT, DocService

router = APIRouter()
_doc_service: DocService | None = None


def mount_doc_routes(doc_service: DocService) -> APIRouter:
    global _doc_service
    _doc_service = doc_service
    return router


def _svc() -> DocService:
    assert _doc_service is not None
    return _doc_service


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

@router.post("/api/projects/{project}/docs/{doc_id}/chats", response_model=DocChatRecord)
def create_doc_chat(project: str, doc_id: str, body: CreateDocChatRequest) -> DocChatRecord:
    try:
        return _svc().create_chat(project, doc_id, title=body.title, mode=body.mode, first_agent=body.first_agent)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/docs/{doc_id}/chats", response_model=list[DocChatListItem])
def list_doc_chats(project: str, doc_id: str) -> list[DocChatListItem]:
    try:
        return _svc().list_chats(project, doc_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}", response_model=DocChatRecord)
def get_doc_chat(project: str, doc_id: str, chat_id: str) -> DocChatRecord:
    try:
        return _svc().get_chat(project, doc_id, chat_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}", response_model=DocChatRecord)
def update_doc_chat(project: str, doc_id: str, chat_id: str, body: UpdateDocChatRequest) -> DocChatRecord:
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
# Background task registry (same pattern as chat_routes)
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


def _task_key(project: str, doc_id: str, chat_id: str) -> str:
    return f"doc/{project}/{doc_id}/{chat_id}"


def _get_task(project: str, doc_id: str, chat_id: str) -> BackgroundTask | None:
    with _tasks_lock:
        return _active_tasks.get(_task_key(project, doc_id, chat_id))


def _set_task(project: str, doc_id: str, chat_id: str, task: BackgroundTask) -> None:
    key = _task_key(project, doc_id, chat_id)
    with _tasks_lock:
        old = _active_tasks.get(key)
        if old and not old.done:
            old.cancel()
        _active_tasks[key] = task


def _remove_task(project: str, doc_id: str, chat_id: str) -> None:
    with _tasks_lock:
        _active_tasks.pop(_task_key(project, doc_id, chat_id), None)


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
    agent: str, prompt: str, system_prompt: str,
    task: BackgroundTask, cancel_event: threading.Event,
) -> None:
    adapter = _get_adapter(agent)
    allowed_tools: list[str] = []
    if agent == "claude":
        allowed_tools = ["WebSearch", "WebFetch"]

    request = AgentRequest(
        role=AgentRole.DISCUSSANT,
        prompt=prompt,
        system_prompt=system_prompt,
        timeout_seconds=120,
        allowed_tools=allowed_tools,
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
        task.push_event({"type": "error", "agent": agent, "error": str(exc), "full_text": "".join(collected)})


def _get_agent_text(task: BackgroundTask, agent: str) -> str:
    events, _ = task.snapshot()
    for ev in events:
        if ev.get("agent") == agent and ev.get("type") in ("done", "error"):
            return ev.get("full_text", "")
    return ""


def _save_agent_result(
    svc: DocService, project: str, doc_id: str, chat_id: str,
    agent: str, task: BackgroundTask,
) -> None:
    text = _get_agent_text(task, agent)
    if text:
        cleaned, artifacts = extract_artifacts(text, agent=agent)
        if artifacts:
            chat_dir = svc.doc_chat_dir(project, doc_id, chat_id)
            for art in artifacts:
                try:
                    write_artifact_file(chat_dir, art)
                except Exception:
                    pass
        svc.append_message(
            project, doc_id, chat_id, "assistant", cleaned,
            agent=agent, artifacts=artifacts if artifacts else None,
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _orchestrate_single(
    svc: DocService, project: str, doc_id: str, chat_id: str,
    agent: str, task: BackgroundTask,
) -> None:
    try:
        prompt = svc.build_prompt(project, doc_id, chat_id)
        cancel_event = threading.Event()
        task.cancel_events.append(cancel_event)
        _run_agent_to_task(agent, prompt, DOC_SYSTEM_PROMPT, task, cancel_event)
        _save_agent_result(svc, project, doc_id, chat_id, agent, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, doc_id, chat_id)


def _orchestrate_parallel(
    svc: DocService, project: str, doc_id: str, chat_id: str,
    agents: list[str], task: BackgroundTask,
) -> None:
    try:
        prompt = svc.build_prompt(project, doc_id, chat_id)
        threads = []
        for agent in agents:
            cancel_event = threading.Event()
            task.cancel_events.append(cancel_event)
            t = threading.Thread(
                target=_run_agent_to_task,
                args=(agent, prompt, DOC_SYSTEM_PROMPT, task, cancel_event),
                daemon=True,
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        for agent in agents:
            _save_agent_result(svc, project, doc_id, chat_id, agent, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, doc_id, chat_id)


def _orchestrate_round_robin(
    svc: DocService, project: str, doc_id: str, chat_id: str,
    agents: list[str], task: BackgroundTask,
) -> None:
    try:
        first = agents[0]
        prompt = svc.build_prompt(project, doc_id, chat_id)
        cancel_event = threading.Event()
        task.cancel_events.append(cancel_event)
        _run_agent_to_task(first, prompt, DOC_SYSTEM_PROMPT, task, cancel_event)
        _save_agent_result(svc, project, doc_id, chat_id, first, task)

        first_text = _get_agent_text(task, first)
        if len(agents) > 1 and first_text:
            second = agents[1]
            prompt2 = svc.build_prompt(project, doc_id, chat_id)
            cancel_event2 = threading.Event()
            task.cancel_events.append(cancel_event2)
            _run_agent_to_task(second, prompt2, DOC_SYSTEM_PROMPT, task, cancel_event2)
            _save_agent_result(svc, project, doc_id, chat_id, second, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, doc_id, chat_id)


# ---------------------------------------------------------------------------
# SSE streaming
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


# ---------------------------------------------------------------------------
# SSE endpoints
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}/ask")
async def doc_ask(project: str, doc_id: str, chat_id: str, body: DocAskRequest):
    svc = _svc()
    try:
        chat = svc.get_chat(project, doc_id, chat_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    svc.append_message(project, doc_id, chat_id, "user", body.content)

    mode = chat.mode
    first = chat.first_agent
    second = "codex" if first == "claude" else "claude"

    task = BackgroundTask()
    _set_task(project, doc_id, chat_id, task)

    from labit.api.chat_models import ChatMode
    if mode == ChatMode.SINGLE:
        target = _orchestrate_single
        args = (svc, project, doc_id, chat_id, first, task)
    elif mode == ChatMode.PARALLEL:
        target = _orchestrate_parallel
        args = (svc, project, doc_id, chat_id, [first, second], task)
    else:
        target = _orchestrate_round_robin
        args = (svc, project, doc_id, chat_id, [first, second], task)

    threading.Thread(target=target, args=args, daemon=True).start()
    return StreamingResponse(_stream_from_task(task), media_type="text/event-stream")


@router.get("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}/active-task")
def get_active_task(project: str, doc_id: str, chat_id: str):
    task = _get_task(project, doc_id, chat_id)
    if not task or task.done:
        return {"active": False}
    events, _ = task.snapshot()
    return {"active": True, "event_count": len(events)}


@router.get("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}/active-task/stream")
async def stream_active_task(project: str, doc_id: str, chat_id: str):
    task = _get_task(project, doc_id, chat_id)
    if not task or task.done:
        raise HTTPException(status_code=404, detail="No active task")
    return StreamingResponse(_stream_from_task(task), media_type="text/event-stream")


@router.post("/api/projects/{project}/docs/{doc_id}/chats/{chat_id}/stop")
def stop_task(project: str, doc_id: str, chat_id: str):
    task = _get_task(project, doc_id, chat_id)
    if task and not task.done:
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}
