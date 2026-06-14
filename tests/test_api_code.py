from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from labit.api.app import create_app
from labit.api.code_service import encode_file_id
from labit.paths import RepoPaths


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
    (paths.vault_projects_dir / name / "code").mkdir(parents=True)
    return paths


def test_code_content_rejects_path_traversal_to_code_sibling(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    project_dir = paths.vault_projects_dir / "Labit"
    code2 = project_dir / "code2"
    code2.mkdir()
    (code2 / "secret.py").write_text("SECRET = True\n", encoding="utf-8")

    client = TestClient(create_app(paths))
    traversal_file_id = encode_file_id("../code2/secret.py")

    response = client.get(f"/api/projects/Labit/code/files/{traversal_file_id}/content")

    assert response.status_code == 404
    assert "path traversal" in response.json()["detail"]


def test_markdown_code_file_preview_renders_pdf(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    code_dir = paths.vault_projects_dir / "Labit" / "code"
    (code_dir / "README.md").write_text("# Test\n\nSome markdown.", encoding="utf-8")

    client = TestClient(create_app(paths))
    file_id = encode_file_id("README.md")

    response = client.get(f"/api/projects/Labit/code/files/{file_id}/preview.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_code_tree_omits_and_rejects_internal_directories(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    code_dir = paths.vault_projects_dir / "Labit" / "code"
    (code_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")

    client = TestClient(create_app(paths))
    create_chat = client.post("/api/projects/Labit/code/chats", json={"file_path": "main.py"})
    assert create_chat.status_code == 200

    response = client.get("/api/projects/Labit/code/tree")

    assert response.status_code == 200
    assert [entry["path"] for entry in response.json()] == ["main.py"]

    internal_response = client.get("/api/projects/Labit/code/tree?path=.chats")
    assert internal_response.status_code == 400
    assert "Internal directories" in internal_response.json()["detail"]


def test_code_chat_can_be_project_level_without_file_path(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    code_dir = paths.vault_projects_dir / "Labit" / "code"
    (code_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")

    client = TestClient(create_app(paths))
    create_chat = client.post("/api/projects/Labit/code/chats", json={})

    assert create_chat.status_code == 200
    chat = create_chat.json()
    assert chat["title"] == "Code chat"
    assert "file_path" not in chat

    all_chats = client.get("/api/projects/Labit/code/chats")
    assert all_chats.status_code == 200
    assert [item["chat_id"] for item in all_chats.json()] == [chat["chat_id"]]

    file_chats = client.get("/api/projects/Labit/code/chats?file_path=main.py")
    assert file_chats.status_code == 200
    assert file_chats.json() == []


def test_code_apply_artifact_requires_requested_file_to_match_chat_file(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    code_dir = paths.vault_projects_dir / "Labit" / "code"
    (code_dir / "a.py").write_text("VALUE = 'a'\n", encoding="utf-8")
    (code_dir / "b.py").write_text("VALUE = 'b'\n", encoding="utf-8")

    client = TestClient(create_app(paths))
    create_chat = client.post("/api/projects/Labit/code/chats", json={"file_path": "a.py"})
    assert create_chat.status_code == 200
    chat = create_chat.json()
    chat_id = chat["chat_id"]
    chat["messages"].append(
        {
            "id": "msg_artifact",
            "role": "assistant",
            "content": "Generated.",
            "agent": "claude",
            "artifacts": [
                {
                    "id": "art_a",
                    "title": "Updated a.py",
                    "filename": "a.py",
                    "language": "python",
                    "mime_type": "text/x-python",
                    "content": "VALUE = 'updated'\n",
                }
            ],
            "created_at": "2026-06-06T00:00:00+00:00",
        }
    )
    chat_path = paths.vault_projects_dir / "Labit" / "code" / ".chats" / chat_id / "chat.json"
    chat_path.write_text(json.dumps(chat), encoding="utf-8")

    b_file_id = encode_file_id("b.py")
    mismatch = client.post(
        f"/api/projects/Labit/code/files/{b_file_id}/apply-artifact",
        json={"chat_id": chat_id, "artifact_id": "art_a"},
    )

    assert mismatch.status_code == 404
    assert "does not belong" in mismatch.json()["detail"]
    assert (code_dir / "b.py").read_text(encoding="utf-8") == "VALUE = 'b'\n"

    a_file_id = encode_file_id("a.py")
    applied = client.post(
        f"/api/projects/Labit/code/files/{a_file_id}/apply-artifact",
        json={"chat_id": chat_id, "artifact_id": "art_a"},
    )

    assert applied.status_code == 200
    assert (code_dir / "a.py").read_text(encoding="utf-8") == "VALUE = 'updated'\n"
    assert any((code_dir / ".history" / a_file_id).iterdir())


def test_code_chat_uploads_and_serves_image_attachment(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    create_chat = client.post("/api/projects/Labit/code/chats", json={})
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat_id"]

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
        b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    upload = client.post(
        f"/api/projects/Labit/code/chats/{chat_id}/attachments",
        files={"file": ("pixel.png", png, "image/png")},
    )

    assert upload.status_code == 200
    attachment = upload.json()
    assert attachment["kind"] == "image"
    assert attachment["filename"] == "pixel.png"
    assert attachment["mime_type"] == "image/png"

    served = client.get(
        f"/api/projects/Labit/code/chats/{chat_id}/attachments/{attachment['id']}"
    )

    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == png


def test_code_chat_rejects_missing_attachment_ids(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    create_chat = client.post("/api/projects/Labit/code/chats", json={})
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat_id"]

    response = client.post(
        f"/api/projects/Labit/code/chats/{chat_id}/ask",
        json={"content": "look at this", "attachment_ids": ["missing"]},
    )

    assert response.status_code == 400
    assert "Attachment not found" in response.json()["detail"]

    chat = client.get(f"/api/projects/Labit/code/chats/{chat_id}")
    assert chat.status_code == 200
    assert chat.json()["messages"] == []


def test_code_chat_rejects_non_image_attachment(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    create_chat = client.post("/api/projects/Labit/code/chats", json={})
    assert create_chat.status_code == 200
    chat_id = create_chat.json()["chat_id"]

    response = client.post(
        f"/api/projects/Labit/code/chats/{chat_id}/attachments",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]
