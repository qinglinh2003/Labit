"""Code API routes: file browsing, content read/write, code-scoped chat with SSE."""
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
from labit.api.code_models import (
    CodeAskRequest,
    CodeChatListItem,
    CodeChatRecord,
    CodeFileContent,
    CodeFileRecord,
    CodeTreeEntry,
    CreateCodeChatRequest,
    UpdateCodeChatRequest,
)
from labit.api.code_service import CODE_SYSTEM_PROMPT, CodeService

router = APIRouter()
_code_service: CodeService | None = None


def mount_code_routes(code_service: CodeService) -> APIRouter:
    global _code_service
    _code_service = code_service
    return router


def _svc() -> CodeService:
    assert _code_service is not None
    return _code_service


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

@router.post("/api/projects/{project}/code/chats", response_model=CodeChatRecord)
def create_code_chat(project: str, body: CreateCodeChatRequest) -> CodeChatRecord:
    try:
        return _svc().create_chat(project, body.file_path, title=body.title, mode=body.mode, first_agent=body.first_agent)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/projects/{project}/code/chats", response_model=list[CodeChatListItem])
def list_code_chats(project: str, file_path: str | None = None) -> list[CodeChatListItem]:
    return _svc().list_chats(project, file_path=file_path)


@router.get("/api/projects/{project}/code/chats/{chat_id}", response_model=CodeChatRecord)
def get_code_chat(project: str, chat_id: str) -> CodeChatRecord:
    try:
        return _svc().get_chat(project, chat_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/api/projects/{project}/code/chats/{chat_id}", response_model=CodeChatRecord)
def update_code_chat(project: str, chat_id: str, body: UpdateCodeChatRequest) -> CodeChatRecord:
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
# Background task registry
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


def _task_key(project: str, chat_id: str) -> str:
    return f"code/{project}/{chat_id}"


def _get_task(project: str, chat_id: str) -> BackgroundTask | None:
    with _tasks_lock:
        return _active_tasks.get(_task_key(project, chat_id))


def _set_task(project: str, chat_id: str, task: BackgroundTask) -> None:
    key = _task_key(project, chat_id)
    with _tasks_lock:
        old = _active_tasks.get(key)
        if old and not old.done:
            old.cancel()
        _active_tasks[key] = task


def _remove_task(project: str, chat_id: str) -> None:
    with _tasks_lock:
        _active_tasks.pop(_task_key(project, chat_id), None)


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
        task.push_event({"type": "error", "agent": agent, "error": str(exc), "full_text": "".join(collected)})


def _get_agent_text(task: BackgroundTask, agent: str) -> str:
    events, _ = task.snapshot()
    for ev in events:
        if ev.get("agent") == agent and ev.get("type") in ("done", "error"):
            return ev.get("full_text", "")
    return ""


def _save_agent_result(
    svc: CodeService, project: str, chat_id: str,
    agent: str, task: BackgroundTask,
) -> None:
    text = _get_agent_text(task, agent)
    if text:
        cleaned, artifacts = extract_artifacts(text, agent=agent)
        if artifacts:
            chat_dir = svc.code_chat_dir(project, chat_id)
            for art in artifacts:
                try:
                    write_artifact_file(chat_dir, art)
                except Exception:
                    pass
        svc.append_message(
            project, chat_id, "assistant", cleaned,
            agent=agent, artifacts=artifacts if artifacts else None,
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _orchestrate_single(
    svc: CodeService, project: str, chat_id: str,
    agent: str, task: BackgroundTask,
) -> None:
    try:
        prompt = svc.build_prompt(project, chat_id)
        code_cwd = svc.get_project_dir(project)
        cancel_event = threading.Event()
        task.cancel_events.append(cancel_event)
        _run_agent_to_task(agent, prompt, CODE_SYSTEM_PROMPT, task, cancel_event, cwd=code_cwd)
        _save_agent_result(svc, project, chat_id, agent, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, chat_id)


def _orchestrate_parallel(
    svc: CodeService, project: str, chat_id: str,
    agents: list[str], task: BackgroundTask,
) -> None:
    try:
        prompt = svc.build_prompt(project, chat_id)
        code_cwd = svc.get_project_dir(project)
        threads = []
        for agent in agents:
            cancel_event = threading.Event()
            task.cancel_events.append(cancel_event)
            t = threading.Thread(
                target=_run_agent_to_task,
                args=(agent, prompt, CODE_SYSTEM_PROMPT, task, cancel_event),
                kwargs={"cwd": code_cwd},
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
    svc: CodeService, project: str, chat_id: str,
    agents: list[str], task: BackgroundTask,
) -> None:
    try:
        first = agents[0]
        code_cwd = svc.get_project_dir(project)
        prompt = svc.build_prompt(project, chat_id)
        cancel_event = threading.Event()
        task.cancel_events.append(cancel_event)
        _run_agent_to_task(first, prompt, CODE_SYSTEM_PROMPT, task, cancel_event, cwd=code_cwd)
        _save_agent_result(svc, project, chat_id, first, task)

        first_text = _get_agent_text(task, first)
        if len(agents) > 1 and first_text:
            second = agents[1]
            prompt2 = svc.build_prompt(project, chat_id)
            cancel_event2 = threading.Event()
            task.cancel_events.append(cancel_event2)
            _run_agent_to_task(second, prompt2, CODE_SYSTEM_PROMPT, task, cancel_event2, cwd=code_cwd)
            _save_agent_result(svc, project, chat_id, second, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, chat_id)


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

@router.post("/api/projects/{project}/code/chats/{chat_id}/ask")
async def code_ask(project: str, chat_id: str, body: CodeAskRequest):
    svc = _svc()
    try:
        chat = svc.get_chat(project, chat_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    svc.append_message(project, chat_id, "user", body.content)

    mode = chat.mode
    first = chat.first_agent
    second = "codex" if first == "claude" else "claude"

    task = BackgroundTask()
    _set_task(project, chat_id, task)

    from labit.api.chat_models import ChatMode
    if mode == ChatMode.SINGLE:
        target = _orchestrate_single
        args = (svc, project, chat_id, first, task)
    elif mode == ChatMode.PARALLEL:
        target = _orchestrate_parallel
        args = (svc, project, chat_id, [first, second], task)
    else:
        target = _orchestrate_round_robin
        args = (svc, project, chat_id, [first, second], task)

    threading.Thread(target=target, args=args, daemon=True).start()
    return StreamingResponse(_stream_from_task(task), media_type="text/event-stream")


@router.get("/api/projects/{project}/code/chats/{chat_id}/active-task")
def get_active_task(project: str, chat_id: str):
    task = _get_task(project, chat_id)
    if not task or task.done:
        return {"active": False}
    events, _ = task.snapshot()
    return {"active": True, "event_count": len(events)}


@router.get("/api/projects/{project}/code/chats/{chat_id}/active-task/stream")
async def stream_active_task(project: str, chat_id: str):
    task = _get_task(project, chat_id)
    if not task or task.done:
        raise HTTPException(status_code=404, detail="No active task")
    return StreamingResponse(_stream_from_task(task), media_type="text/event-stream")


@router.post("/api/projects/{project}/code/chats/{chat_id}/stop")
def stop_task(project: str, chat_id: str):
    task = _get_task(project, chat_id)
    if task and not task.done:
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}
