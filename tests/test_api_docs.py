from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from labit.api.app import create_app
from labit.api.doc_service import encode_doc_id
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
    (paths.vault_projects_dir / name / "docs").mkdir(parents=True)
    return paths


def test_doc_content_rejects_path_traversal_to_docs_sibling(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    project_dir = paths.vault_projects_dir / "Labit"
    docs2 = project_dir / "docs2"
    docs2.mkdir()
    (docs2 / "secret.md").write_text("secret", encoding="utf-8")

    client = TestClient(create_app(paths))
    traversal_doc_id = encode_doc_id("../docs2/secret.md")

    response = client.get(f"/api/projects/Labit/docs/{traversal_doc_id}/content")

    assert response.status_code == 404
    assert "path traversal" in response.json()["detail"]


def test_doc_list_omits_internal_chat_artifacts(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    docs_dir = paths.vault_projects_dir / "Labit" / "docs"
    (docs_dir / "ideas").mkdir()
    (docs_dir / "ideas" / "test.md").write_text("# Test\n", encoding="utf-8")

    client = TestClient(create_app(paths))
    doc_id = encode_doc_id("ideas/test.md")
    create_chat = client.post(f"/api/projects/Labit/docs/{doc_id}/chats", json={})
    assert create_chat.status_code == 200

    response = client.get("/api/projects/Labit/docs")

    assert response.status_code == 200
    paths = [doc["path"] for doc in response.json()]
    assert paths == ["ideas/test.md"]


def test_invalid_doc_id_returns_404_for_doc_chat_routes(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))

    response = client.get("/api/projects/Labit/docs/not-base64/chats")

    assert response.status_code == 404
    assert "Invalid doc_id" in response.json()["detail"]


def test_doc_chat_delete_removes_new_and_legacy_records(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    docs_dir = paths.vault_projects_dir / "Labit" / "docs"
    (docs_dir / "ideas").mkdir()
    (docs_dir / "ideas" / "test.md").write_text("# Test\n", encoding="utf-8")

    client = TestClient(create_app(paths))
    doc_id = encode_doc_id("ideas/test.md")
    create_response = client.post(f"/api/projects/Labit/docs/{doc_id}/chats", json={})
    assert create_response.status_code == 200
    chat = create_response.json()
    chat_id = chat["chat_id"]

    chats_dir = docs_dir / ".chats" / doc_id
    legacy_path = chats_dir / f"{chat_id}.json"
    legacy_path.write_text(json.dumps(chat), encoding="utf-8")

    delete_response = client.delete(f"/api/projects/Labit/docs/{doc_id}/chats/{chat_id}")

    assert delete_response.status_code == 200
    assert not (chats_dir / chat_id).exists()
    assert not legacy_path.exists()
    list_response = client.get(f"/api/projects/Labit/docs/{doc_id}/chats")
    assert list_response.status_code == 200
    assert list_response.json() == []
