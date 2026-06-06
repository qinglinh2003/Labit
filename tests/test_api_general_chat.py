from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from labit.api.app import create_app
from labit.api.general_chat_service import extract_artifacts
from labit.paths import RepoPaths


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


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


def test_general_chat_upload_serves_and_sends_image_attachment(tmp_path: Path, monkeypatch) -> None:
    paths = _create_project(tmp_path)
    captured: dict[str, list[str]] = {}

    def fake_orchestrate_single(svc, project, chat_id, agent, task, image_paths=None):
        captured["image_paths"] = image_paths or []
        task.push_event({"type": "end"})
        task.mark_done()

    monkeypatch.setattr("labit.api.general_chat_routes._orchestrate_single", fake_orchestrate_single)

    client = TestClient(create_app(paths))
    create_response = client.post("/api/projects/Labit/chats", json={"mode": "single"})
    assert create_response.status_code == 200
    chat_id = create_response.json()["chat_id"]

    upload_response = client.post(
        f"/api/projects/Labit/chats/{chat_id}/attachments",
        files={"file": ("screenshot.not-png", PNG_BYTES, "image/png")},
    )
    assert upload_response.status_code == 200
    attachment = upload_response.json()
    assert attachment["filename"] == "screenshot.not-png"
    assert attachment["mime_type"] == "image/png"
    assert attachment["path"].endswith(".png")

    image_response = client.get(f"/api/projects/Labit/chats/{chat_id}/attachments/{attachment['id']}")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert image_response.content == PNG_BYTES

    ask_response = client.post(
        f"/api/projects/Labit/chats/{chat_id}/ask",
        json={"content": "what is in this image?", "attachment_ids": [attachment["id"]]},
    )
    assert ask_response.status_code == 200
    assert captured["image_paths"] == [attachment["path"]]

    chat_response = client.get(f"/api/projects/Labit/chats/{chat_id}")
    assert chat_response.status_code == 200
    user_message = chat_response.json()["messages"][0]
    assert user_message["attachments"][0]["id"] == attachment["id"]


def test_general_chat_rejects_unknown_attachment_id(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    create_response = client.post("/api/projects/Labit/chats", json={"mode": "single"})
    chat_id = create_response.json()["chat_id"]

    ask_response = client.post(
        f"/api/projects/Labit/chats/{chat_id}/ask",
        json={"content": "use this", "attachment_ids": ["missing"]},
    )

    assert ask_response.status_code == 400
    assert "missing" in ask_response.json()["detail"]


def test_general_chat_extracts_artifact_blocks() -> None:
    cleaned, artifacts = extract_artifacts(
        "Here is the file.\n\n"
        "```artifact:../proposal.md\n"
        "title: Project Proposal\n"
        "---\n"
        "# Project Proposal\n\n"
        "Draft body.\n"
        "```\n\n"
        "Done.",
        agent="claude",
    )

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.filename == "proposal.md"
    assert artifact.title == "Project Proposal"
    assert artifact.language == "markdown"
    assert artifact.mime_type == "text/markdown"
    assert artifact.content == "# Project Proposal\n\nDraft body."
    assert "```artifact:" not in cleaned
    assert "Project Proposal" in cleaned


def test_general_chat_extracts_artifacts_with_nested_code_fences() -> None:
    cleaned, artifacts = extract_artifacts(
        "Here is the file.\n\n"
        "`````artifact:proposal.md\n"
        "title: Project Proposal\n"
        "---\n"
        "# Project Proposal\n\n"
        "```python\n"
        "print('hello')\n"
        "```\n\n"
        "## Conclusion\n"
        "The parser should not stop at the nested code fence.\n"
        "`````\n\n"
        "Done.",
        agent="codex",
    )

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.filename == "proposal.md"
    assert "```python\nprint('hello')\n```" in artifact.content
    assert "## Conclusion" in artifact.content
    assert "nested code fence" not in cleaned


def test_general_chat_downloads_artifact_from_chat(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    create_response = client.post("/api/projects/Labit/chats", json={"mode": "single"})
    assert create_response.status_code == 200
    chat = create_response.json()
    chat_id = chat["chat_id"]

    chat["messages"].append(
        {
            "id": "msg_artifact",
            "role": "assistant",
            "content": "Generated.",
            "agent": "claude",
            "attachments": [],
            "artifacts": [
                {
                    "id": "art_test",
                    "title": "Project Proposal",
                    "filename": '研究"方案.md',
                    "language": "markdown",
                    "mime_type": "text/markdown",
                    "content": "# Project Proposal\n\nDraft body.",
                }
            ],
            "created_at": "2026-06-06T00:00:00+00:00",
        }
    )
    chat_path = paths.vault_projects_dir / "Labit" / "chats" / chat_id / "chat.json"
    chat_path.write_text(json.dumps(chat), encoding="utf-8")

    response = client.get(f"/api/projects/Labit/chats/{chat_id}/artifacts/art_test/download")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    content_disposition = response.headers["content-disposition"]
    assert 'filename="??_??.md"' in content_disposition
    assert "filename*=UTF-8''%E7%A0%94%E7%A9%B6%22%E6%96%B9%E6%A1%88.md" in content_disposition
    assert response.text == "# Project Proposal\n\nDraft body."

    missing_response = client.get(
        f"/api/projects/Labit/chats/{chat_id}/artifacts/missing/download"
    )
    assert missing_response.status_code == 404

    missing_chat_response = client.get(
        "/api/projects/Labit/chats/missing/artifacts/art_test/download"
    )
    assert missing_chat_response.status_code == 404


def test_general_chat_delete_removes_new_and_legacy_records(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    create_response = client.post("/api/projects/Labit/chats", json={"mode": "single"})
    assert create_response.status_code == 200
    chat = create_response.json()
    chat_id = chat["chat_id"]

    chats_dir = paths.vault_projects_dir / "Labit" / "chats"
    legacy_path = chats_dir / f"{chat_id}.json"
    legacy_attachments = chats_dir / f"{chat_id}_attachments"
    legacy_path.write_text(json.dumps(chat), encoding="utf-8")
    legacy_attachments.mkdir()
    (legacy_attachments / "old.png").write_bytes(PNG_BYTES)

    delete_response = client.delete(f"/api/projects/Labit/chats/{chat_id}")

    assert delete_response.status_code == 200
    assert not (chats_dir / chat_id).exists()
    assert not legacy_path.exists()
    assert not legacy_attachments.exists()
    list_response = client.get("/api/projects/Labit/chats")
    assert list_response.status_code == 200
    assert list_response.json() == []
