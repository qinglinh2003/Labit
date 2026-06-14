from __future__ import annotations

import threading
from pathlib import Path

from labit.api.agent_runtime import BackgroundTask, OrchestrationConfig, TaskRegistry, orchestrate
from labit.api.chat_models import ChatMode


def test_orchestration_system_prompt_includes_superpowers_workflow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "vault" / "projects" / "Labit"
    project_dir.mkdir(parents=True)
    captured: dict[str, str] = {}

    def fake_run_agent_to_task(
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
        captured["system_prompt"] = system_prompt
        task.push_event({"type": "done", "agent": agent, "content": ""})

    monkeypatch.setattr("labit.api.agent_runtime.run_agent_to_task", fake_run_agent_to_task)

    task = BackgroundTask()
    registry = TaskRegistry()
    registry.set("task", task)
    orchestrate(
        ChatMode.SINGLE,
        ["codex"],
        task,
        registry,
        "task",
        OrchestrationConfig(
            system_prompt="Base prompt.",
            allowed_tools=[],
            cwd=str(project_dir),
            project="Labit",
            compute_profiles=[],
        ),
        build_prompt=lambda agent: "User prompt.",
        save_result=lambda agent: None,
    )

    system_prompt = captured["system_prompt"]
    assert "# SUPERPOWERS WORKFLOW" in system_prompt
    assert "brainstorming" in system_prompt
    assert "writing-plans" in system_prompt
    assert "test-driven-development" in system_prompt
    assert "systematic-debugging" in system_prompt
    assert "verification-before-completion" in system_prompt
    assert "current user message remains authoritative" in system_prompt
    assert "Do not create git worktrees" in system_prompt
    assert "branch-finishing workflows" in system_prompt
