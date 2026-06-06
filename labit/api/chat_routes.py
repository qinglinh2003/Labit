"""Chat API routes: CRUD + SSE streaming via agent subprocess.

Agent tasks run in background threads, decoupled from SSE connections.
Navigating away no longer kills the agent — the task keeps running and
messages are saved when the agent finishes.  The frontend can reconnect
to an in-progress task via GET .../active-task/stream.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from labit.api.artifact_storage import write_artifact_file
from labit.api.downloads import attachment_content_disposition
from labit.api.general_chat_service import extract_artifacts

from labit.agents.adapters.base import AgentAdapterError, StreamCancelled
from labit.agents.adapters.claude import ClaudeAdapter
from labit.agents.adapters.codex import CodexAdapter
from labit.agents.models import AgentRequest, AgentRole
from labit.api.chat_models import (
    AskRequest,
    ChatListItem,
    ChatMode,
    ChatRecord,
    CreateChatRequest,
    UpdateChatRequest,
)
from labit.api.chat_service import SYSTEM_PROMPT, ChatService

router = APIRouter()

# Module-level service — set by mount_chat_routes()
_chat_service: ChatService | None = None


def mount_chat_routes(chat_service: ChatService) -> APIRouter:
    global _chat_service
    _chat_service = chat_service
    return router


def _svc() -> ChatService:
    assert _chat_service is not None
    return _chat_service


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
# Background task registry
# ---------------------------------------------------------------------------

@dataclass
class BackgroundTask:
    """An agent task that runs independently of any SSE connection."""

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


# chat_key -> BackgroundTask
_active_tasks: dict[str, BackgroundTask] = {}
_tasks_lock = threading.Lock()


def _chat_key(project: str, paper_id: str, chat_id: str) -> str:
    return f"{project}/{paper_id}/{chat_id}"


def _get_task(project: str, paper_id: str, chat_id: str) -> BackgroundTask | None:
    with _tasks_lock:
        return _active_tasks.get(_chat_key(project, paper_id, chat_id))


def _set_task(project: str, paper_id: str, chat_id: str, task: BackgroundTask) -> None:
    key = _chat_key(project, paper_id, chat_id)
    with _tasks_lock:
        old = _active_tasks.get(key)
        if old and not old.done:
            old.cancel()
        _active_tasks[key] = task


def _remove_task(project: str, paper_id: str, chat_id: str) -> None:
    key = _chat_key(project, paper_id, chat_id)
    with _tasks_lock:
        _active_tasks.pop(key, None)


# ---------------------------------------------------------------------------
# Agent execution (runs in background threads)
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
    *,
    cwd: str | None = None,
) -> None:
    """Run agent subprocess, pushing events to a BackgroundTask."""
    adapter = _get_adapter(agent)
    allowed_tools: list[str] = []
    extra_args: list[str] = []
    if agent == "claude":
        allowed_tools = ["Read", "Grep", "Glob", "WebSearch", "WebFetch"]

    request = AgentRequest(
        role=AgentRole.DISCUSSANT,
        prompt=prompt,
        system_prompt=system_prompt,
        timeout_seconds=120,
        allowed_tools=allowed_tools,
        extra_args=extra_args,
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
    """Extract the full_text for an agent from task events."""
    events, _done = task.snapshot()
    for ev in events:
        if ev.get("agent") == agent and ev.get("type") in ("done", "error"):
            return ev.get("full_text", "")
    return ""


def _save_agent_result(
    svc: ChatService, project: str, paper_id: str, chat_id: str,
    agent: str, task: BackgroundTask,
) -> None:
    text = _get_agent_text(task, agent)
    if text:
        cleaned, artifacts = extract_artifacts(text, agent=agent)
        if artifacts:
            chat_dir = svc.chat_dir(project, paper_id, chat_id)
            for art in artifacts:
                try:
                    write_artifact_file(chat_dir, art)
                except Exception:
                    pass
        svc.append_message(
            project, paper_id, chat_id, "assistant", cleaned,
            agent=agent, artifacts=artifacts if artifacts else None,
        )


# ---------------------------------------------------------------------------
# Orchestration threads (one per mode)
# ---------------------------------------------------------------------------

def _orchestrate_single(
    svc: ChatService, project: str, paper_id: str, chat_id: str,
    agent: str, task: BackgroundTask,
) -> None:
    try:
        prompt = svc.build_prompt(project, paper_id, chat_id)
        project_cwd = svc.get_project_dir(project)
        cancel_event = threading.Event()
        task.cancel_events.append(cancel_event)
        _run_agent_to_task(agent, prompt, SYSTEM_PROMPT, task, cancel_event, cwd=project_cwd)
        _save_agent_result(svc, project, paper_id, chat_id, agent, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, paper_id, chat_id)


def _orchestrate_parallel(
    svc: ChatService, project: str, paper_id: str, chat_id: str,
    agents: list[str], task: BackgroundTask,
) -> None:
    try:
        prompt = svc.build_prompt(project, paper_id, chat_id)
        project_cwd = svc.get_project_dir(project)
        threads = []
        for agent in agents:
            cancel_event = threading.Event()
            task.cancel_events.append(cancel_event)
            t = threading.Thread(
                target=_run_agent_to_task,
                args=(agent, prompt, SYSTEM_PROMPT, task, cancel_event),
                kwargs={"cwd": project_cwd},
                daemon=True,
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        for agent in agents:
            _save_agent_result(svc, project, paper_id, chat_id, agent, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, paper_id, chat_id)


def _orchestrate_round_robin(
    svc: ChatService, project: str, paper_id: str, chat_id: str,
    agents: list[str], task: BackgroundTask,
) -> None:
    try:
        # First agent
        first = agents[0]
        prompt = svc.build_prompt(project, paper_id, chat_id)
        project_cwd = svc.get_project_dir(project)
        cancel_event = threading.Event()
        task.cancel_events.append(cancel_event)
        _run_agent_to_task(first, prompt, SYSTEM_PROMPT, task, cancel_event, cwd=project_cwd)
        _save_agent_result(svc, project, paper_id, chat_id, first, task)

        first_text = _get_agent_text(task, first)
        if len(agents) > 1 and first_text:
            second = agents[1]
            prompt2 = svc.build_prompt(project, paper_id, chat_id)
            cancel_event2 = threading.Event()
            task.cancel_events.append(cancel_event2)
            _run_agent_to_task(second, prompt2, SYSTEM_PROMPT, task, cancel_event2, cwd=project_cwd)
            _save_agent_result(svc, project, paper_id, chat_id, second, task)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        _remove_task(project, paper_id, chat_id)


# ---------------------------------------------------------------------------
# SSE stream from task
# ---------------------------------------------------------------------------

async def _stream_from_task(task: BackgroundTask) -> AsyncGenerator[str, None]:
    """Read events from a BackgroundTask and yield as SSE."""
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

@router.post("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}/ask")
async def ask(project: str, paper_id: str, chat_id: str, body: AskRequest):
    svc = _svc()
    try:
        chat = svc.get_chat(project, paper_id, chat_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Save user message first
    svc.append_message(project, paper_id, chat_id, "user", body.content)

    mode = chat.mode
    first = chat.first_agent
    second = "codex" if first == "claude" else "claude"

    # Create background task
    task = BackgroundTask()
    _set_task(project, paper_id, chat_id, task)

    # Pick orchestration function
    if mode == ChatMode.SINGLE:
        target = _orchestrate_single
        args = (svc, project, paper_id, chat_id, first, task)
    elif mode == ChatMode.PARALLEL:
        target = _orchestrate_parallel
        args = (svc, project, paper_id, chat_id, [first, second], task)
    else:  # round_robin
        target = _orchestrate_round_robin
        args = (svc, project, paper_id, chat_id, [first, second], task)

    # Start orchestration in background thread
    threading.Thread(target=target, args=args, daemon=True).start()

    return StreamingResponse(_stream_from_task(task), media_type="text/event-stream")


@router.get("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}/active-task")
def get_active_task(project: str, paper_id: str, chat_id: str):
    """Check if there is an active (running) task for this chat."""
    task = _get_task(project, paper_id, chat_id)
    if not task or task.done:
        return {"active": False}
    events, _done = task.snapshot()
    return {"active": True, "event_count": len(events)}


@router.get("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}/active-task/stream")
async def stream_active_task(project: str, paper_id: str, chat_id: str):
    """Reconnect to an in-progress task's SSE stream."""
    task = _get_task(project, paper_id, chat_id)
    if not task or task.done:
        raise HTTPException(status_code=404, detail="No active task")
    return StreamingResponse(_stream_from_task(task), media_type="text/event-stream")


@router.post("/api/projects/{project}/papers/{paper_id}/chats/{chat_id}/stop")
def stop_task(project: str, paper_id: str, chat_id: str):
    """Explicitly cancel the running task."""
    task = _get_task(project, paper_id, chat_id)
    if task and not task.done:
        task.cancel()
        return {"stopped": True}
    return {"stopped": False}
