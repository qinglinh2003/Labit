"""Shared agent runtime: BackgroundTask, TaskRegistry, agent execution, orchestration.

Extracted from general_chat_routes / code_routes / chat_routes / doc_routes to
eliminate ~850 lines of copy-paste duplication.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import Path

from labit.agents.adapters.base import AgentAdapterError, StreamCancelled
from labit.agents.adapters.claude import ClaudeAdapter
from labit.agents.adapters.codex import CodexAdapter
from labit.agents.models import AgentRequest, AgentRole
from labit.api.artifact_storage import extract_artifacts, write_artifact_file
from labit.api.chat_models import ChatMode
from labit.api.shared_prompts import (
    compute_context,
    mode_participants_context,
    project_identity_context,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BackgroundTask
# ---------------------------------------------------------------------------

CODEX_MAX_HISTORY = 20  # Codex fails with very long prompts; keep last N messages


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


# ---------------------------------------------------------------------------
# TaskRegistry
# ---------------------------------------------------------------------------


class TaskRegistry:
    """Thread-safe registry of active BackgroundTasks, keyed by string."""

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> BackgroundTask | None:
        with self._lock:
            return self._tasks.get(key)

    def set(self, key: str, task: BackgroundTask) -> None:
        with self._lock:
            old = self._tasks.get(key)
            if old and not old.done:
                old.cancel()
            self._tasks[key] = task

    def remove(self, key: str) -> None:
        with self._lock:
            self._tasks.pop(key, None)


# ---------------------------------------------------------------------------
# Agent adapter factory
# ---------------------------------------------------------------------------


def get_adapter(agent: str):
    if agent == "claude":
        return ClaudeAdapter()
    elif agent == "codex":
        return CodexAdapter()
    raise ValueError(f"Unknown agent: {agent}")


# ---------------------------------------------------------------------------
# Agent execution
# ---------------------------------------------------------------------------


def run_agent_to_task(
    agent: str,
    prompt: str,
    system_prompt: str,
    task: BackgroundTask,
    cancel_event: threading.Event,
    *,
    allowed_tools: list[str],
    image_paths: list[str] | None = None,
    cwd: str | None = None,
) -> None:
    """Run agent subprocess, pushing events to a BackgroundTask."""
    adapter = get_adapter(agent)

    request = AgentRequest(
        role=AgentRole.DISCUSSANT,
        prompt=prompt,
        system_prompt=system_prompt,
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
        response = adapter.run_stream(
            request, on_text=on_text, on_status=on_status, cancel_event=cancel_event,
        )
        final_text = response.raw_output.strip() or "".join(collected)
        task.push_event({"type": "done", "agent": agent, "full_text": final_text})
    except StreamCancelled:
        task.push_event({"type": "done", "agent": agent, "full_text": "".join(collected)})
    except AgentAdapterError as exc:
        task.push_event({"type": "error", "agent": agent, "error": str(exc), "full_text": "".join(collected)})
    except Exception as exc:
        task.push_event({"type": "error", "agent": agent, "error": str(exc), "full_text": "".join(collected)})


def get_agent_text(task: BackgroundTask, agent: str) -> str:
    """Extract the full_text for an agent from task events."""
    events, _done = task.snapshot()
    for ev in events:
        if ev.get("agent") == agent and ev.get("type") in ("done", "error"):
            return ev.get("full_text", "")
    return ""


# ---------------------------------------------------------------------------
# Save agent result (shared helper)
# ---------------------------------------------------------------------------


def save_agent_result(
    task: BackgroundTask,
    agent: str,
    chat_dir: Path,
    append_message_fn: Callable,
) -> None:
    """Extract agent text, parse artifacts, write files, and persist message.

    ``append_message_fn`` is called as ``append_message_fn(role, content, agent=, artifacts=)``.
    Each routes module provides a closure that binds the service + locator args.
    """
    text = get_agent_text(task, agent)
    if text:
        cleaned, artifacts = extract_artifacts(text, agent=agent)
        if artifacts:
            for art in artifacts:
                try:
                    write_artifact_file(chat_dir, art)
                except Exception:
                    logger.exception("Failed to write artifact file for %s", agent)
        try:
            append_message_fn(
                "assistant", cleaned,
                agent=agent, artifacts=artifacts if artifacts else None,
            )
        except Exception:
            logger.exception("Failed to save agent message for %s", agent)
            raise


# ---------------------------------------------------------------------------
# SSE streaming
# ---------------------------------------------------------------------------


async def stream_from_task(task: BackgroundTask) -> AsyncGenerator[str, None]:
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
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class OrchestrationConfig:
    """Domain-specific configuration for an orchestration run."""

    system_prompt: str          # base domain system prompt (e.g. SYSTEM_PROMPT, CODE_SYSTEM_PROMPT)
    allowed_tools: list[str]
    cwd: str | None = None
    image_paths: list[str] | None = None
    project: str = ""
    compute_profiles: list = field(default_factory=list)


def orchestrate(
    mode: ChatMode,
    agents: list[str],
    task: BackgroundTask,
    registry: TaskRegistry,
    task_key: str,
    config: OrchestrationConfig,
    *,
    build_prompt: Callable[[str], str],
    save_result: Callable[[str], None],
) -> None:
    """Unified single/parallel/round_robin orchestration.

    ``build_prompt(agent_name)`` returns the full conversation prompt for that agent.
    ``save_result(agent_name)`` persists the agent's output (calls save_agent_result internally).
    """
    try:
        proj_ctx = project_identity_context(config.project, config.cwd)
        comp_ctx = compute_context(config.compute_profiles)
        sys_base = config.system_prompt + mode_participants_context(mode.value, agents) + proj_ctx + comp_ctx

        if mode == ChatMode.SINGLE:
            _orchestrate_single(agents[0], task, sys_base, config, build_prompt, save_result)
        elif mode == ChatMode.PARALLEL:
            _orchestrate_parallel(agents, task, sys_base, config, build_prompt, save_result)
        else:
            _orchestrate_round_robin(agents, task, sys_base, config, build_prompt, save_result)
    except Exception as exc:
        task.push_event({"type": "error", "error": str(exc)})
    finally:
        task.push_event({"type": "end"})
        task.mark_done()
        registry.remove(task_key)


def _orchestrate_single(
    agent: str,
    task: BackgroundTask,
    sys_prompt: str,
    config: OrchestrationConfig,
    build_prompt: Callable[[str], str],
    save_result: Callable[[str], None],
) -> None:
    prompt = build_prompt(agent)
    cancel_event = threading.Event()
    task.cancel_events.append(cancel_event)
    run_agent_to_task(
        agent, prompt, sys_prompt, task, cancel_event,
        allowed_tools=config.allowed_tools,
        image_paths=config.image_paths,
        cwd=config.cwd,
    )
    save_result(agent)


def _orchestrate_parallel(
    agents: list[str],
    task: BackgroundTask,
    sys_prompt: str,
    config: OrchestrationConfig,
    build_prompt: Callable[[str], str],
    save_result: Callable[[str], None],
) -> None:
    threads = []
    for agent in agents:
        prompt = build_prompt(agent)
        cancel_event = threading.Event()
        task.cancel_events.append(cancel_event)
        t = threading.Thread(
            target=run_agent_to_task,
            args=(agent, prompt, sys_prompt, task, cancel_event),
            kwargs={
                "allowed_tools": config.allowed_tools,
                "image_paths": config.image_paths,
                "cwd": config.cwd,
            },
            daemon=True,
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    for agent in agents:
        save_result(agent)


def _orchestrate_round_robin(
    agents: list[str],
    task: BackgroundTask,
    sys_prompt: str,
    config: OrchestrationConfig,
    build_prompt: Callable[[str], str],
    save_result: Callable[[str], None],
) -> None:
    first = agents[0]
    prompt = build_prompt(first)
    cancel_event = threading.Event()
    task.cancel_events.append(cancel_event)
    run_agent_to_task(
        first, prompt, sys_prompt, task, cancel_event,
        allowed_tools=config.allowed_tools,
        image_paths=config.image_paths,
        cwd=config.cwd,
    )
    save_result(first)

    first_text = get_agent_text(task, first)
    if len(agents) > 1 and first_text:
        second = agents[1]
        # After save_result(first), the first agent's reply is in chat history.
        # build_prompt(second) will include it, so no need for extra peer context.
        prompt2 = build_prompt(second)
        cancel_event2 = threading.Event()
        task.cancel_events.append(cancel_event2)
        # Don't re-send images for the second agent in round robin
        run_agent_to_task(
            second, prompt2, sys_prompt, task, cancel_event2,
            allowed_tools=config.allowed_tools,
            cwd=config.cwd,
        )
        save_result(second)
