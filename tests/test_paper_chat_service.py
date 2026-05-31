from __future__ import annotations

from pathlib import Path
import asyncio
import threading

import pytest
import yaml
from pydantic import ValidationError

from labit.api.chat_models import CreateChatRequest
from labit.api.chat_routes import BackgroundTask, _stream_from_task
from labit.api.chat_service import ChatService
from labit.papers.models import PaperRecord
from labit.papers.service import PaperService
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


def _paths(root: Path) -> RepoPaths:
    return RepoPaths(
        root=root,
        labit_dir=root / ".labit",
        runs_dir=root / ".labit" / "runs",
        conversations_dir=root / ".labit" / "conversations",
        context_dir=root / ".labit" / "context",
        configs_dir=root / "configs",
        project_configs_dir=root / "configs" / "projects",
        active_project_path=root / "configs" / "active_project",
        vault_dir=root / "vault",
        vault_projects_dir=root / "vault" / "projects",
    )


def _create_project(root: Path, name: str = "Labit") -> RepoPaths:
    paths = _paths(root)
    paths.project_configs_dir.mkdir(parents=True)
    paths.vault_projects_dir.mkdir(parents=True)
    paths.configs_dir.mkdir(exist_ok=True)
    (paths.project_configs_dir / f"{name}.yaml").write_text(
        yaml.safe_dump({"name": name}),
        encoding="utf-8",
    )
    paths.active_project_path.write_text(f"{name}\n", encoding="utf-8")
    (paths.vault_projects_dir / name).mkdir()
    return paths


def _write_paper(paths: RepoPaths, project: str = "Labit") -> None:
    paper_dir = paths.vault_projects_dir / project / "papers" / "arxiv-2401.12345"
    artifacts_dir = paper_dir / "artifacts"
    (artifacts_dir / "text").mkdir(parents=True)
    record = PaperRecord(
        id="arxiv:2401.12345",
        arxiv_id="2401.12345",
        title="A Useful Paper",
        authors=["Ada Lovelace"],
        abstract="A compact abstract.",
        source_url="https://arxiv.org/abs/2401.12345",
        pdf_url="https://arxiv.org/pdf/2401.12345",
        local_metadata_path=str((paper_dir / "paper.yaml").relative_to(paths.root)),
        artifact_dir_path=str(artifacts_dir.relative_to(paths.root)),
        added_at="2026-05-30T14:00:00+00:00",
    )
    (paper_dir / "paper.yaml").write_text(
        yaml.safe_dump(record.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    (artifacts_dir / "text" / "full.txt").write_text(
        "This is the extracted paper text.",
        encoding="utf-8",
    )


def test_build_prompt_uses_persisted_history_without_duplicating_current_question(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    _write_paper(paths)
    paper_service = PaperService(paths, project_service=ProjectService(paths))
    chat_service = ChatService(paper_service)
    chat = chat_service.create_chat("Labit", "arxiv:2401.12345")

    chat_service.append_message(
        "Labit",
        "arxiv:2401.12345",
        chat.chat_id,
        "user",
        "What is the main idea?",
    )

    prompt = chat_service.build_prompt("Labit", "arxiv:2401.12345", chat.chat_id)

    assert prompt.count("What is the main idea?") == 1
    assert "This is the extracted paper text." in prompt


def test_chat_request_rejects_unknown_first_agent() -> None:
    with pytest.raises(ValidationError):
        CreateChatRequest(first_agent="gemini")


def test_closing_sse_stream_does_not_cancel_background_task() -> None:
    cancel_event = threading.Event()
    task = BackgroundTask(cancel_events=[cancel_event])
    task.push_event({"type": "start", "agent": "claude"})

    async def consume_one_event_and_close() -> None:
        stream = _stream_from_task(task)
        first = await anext(stream)
        assert "event: start" in first
        await stream.aclose()

    asyncio.run(consume_one_event_and_close())

    assert not cancel_event.is_set()
