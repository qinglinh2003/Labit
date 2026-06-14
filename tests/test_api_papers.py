from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from labit.api.app import create_app
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
    (paths.vault_projects_dir / name).mkdir()
    return paths


def test_api_imports_lists_and_serves_project_paper(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    metadata = {
        "arxiv_id": "2401.12345",
        "title": "A Useful Paper",
        "authors": ["Ada Lovelace"],
        "abstract": "A compact abstract.",
        "url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
        "submitted_date": "2024-01-02",
    }

    import_response = client.post(
        "/api/projects/Labit/papers/import/arxiv",
        data={"metadata": json.dumps(metadata)},
        files={"pdf": ("paper.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    assert import_response.status_code == 200
    assert import_response.json()["id"] == "arxiv:2401.12345"
    assert import_response.json()["submitted_date"] == "2024-01-02"

    list_response = client.get("/api/projects/Labit/papers")
    assert list_response.status_code == 200
    assert [item["title"] for item in list_response.json()] == ["A Useful Paper"]

    detail_response = client.get("/api/projects/Labit/papers/arxiv-2401.12345")
    assert detail_response.status_code == 200
    assert detail_response.json()["local_pdf_path"].endswith("/paper.pdf")

    pdf_response = client.get("/api/projects/Labit/papers/arxiv-2401.12345/pdf")
    assert pdf_response.status_code == 200
    assert pdf_response.content == b"%PDF-1.7\n"
    assert pdf_response.headers["accept-ranges"] == "bytes"
    assert pdf_response.headers["cache-control"] == "public, max-age=86400, immutable"

    artifacts_response = client.get("/api/projects/Labit/papers/arxiv-2401.12345/artifacts")
    assert artifacts_response.status_code == 200
    assert artifacts_response.json()[0]["relative_path"] == "notes.md"


def test_api_updates_project_paper_tags(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    metadata = {
        "arxiv_id": "2401.12345",
        "title": "A Useful Paper",
        "authors": ["Ada Lovelace"],
        "abstract": "A compact abstract.",
        "url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
    }
    client.post(
        "/api/projects/Labit/papers/import/arxiv",
        data={"metadata": json.dumps(metadata)},
        files={"pdf": ("paper.pdf", b"%PDF-1.7\n", "application/pdf")},
    )

    response = client.patch(
        "/api/projects/Labit/papers/arxiv-2401.12345/tags",
        json={"tags": [" LLM ", "biology", "llm", "", "  "]},
    )

    assert response.status_code == 200
    assert response.json()["tags"] == ["llm", "biology"]
    metadata_path = tmp_path / "vault" / "projects" / "Labit" / "papers" / "arxiv-2401.12345" / "paper.yaml"
    assert yaml.safe_load(metadata_path.read_text(encoding="utf-8"))["tags"] == ["llm", "biology"]


def test_api_toggles_project_paper_star(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    metadata = {
        "arxiv_id": "2401.12345",
        "title": "A Useful Paper",
        "authors": ["Ada Lovelace"],
        "abstract": "A compact abstract.",
        "url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
    }
    client.post(
        "/api/projects/Labit/papers/import/arxiv",
        data={"metadata": json.dumps(metadata)},
        files={"pdf": ("paper.pdf", b"%PDF-1.7\n", "application/pdf")},
    )

    starred_response = client.put("/api/projects/Labit/papers/arxiv-2401.12345/star")
    unstarred_response = client.put("/api/projects/Labit/papers/2401.12345/star")

    assert starred_response.status_code == 200
    assert starred_response.json()["starred"] is True
    assert unstarred_response.status_code == 200
    assert unstarred_response.json()["starred"] is False
    metadata_path = tmp_path / "vault" / "projects" / "Labit" / "papers" / "arxiv-2401.12345" / "paper.yaml"
    assert yaml.safe_load(metadata_path.read_text(encoding="utf-8"))["starred"] is False


def test_api_paper_list_hides_rejected_unless_requested(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    first_metadata = {
        "arxiv_id": "2401.12345",
        "title": "Rejected Paper",
        "authors": ["Ada Lovelace"],
        "abstract": "A compact abstract.",
        "url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
    }
    second_metadata = {
        "arxiv_id": "2401.54321",
        "title": "Useful Paper",
        "authors": ["Grace Hopper"],
        "abstract": "Another compact abstract.",
        "url": "https://arxiv.org/abs/2401.54321",
        "pdf_url": "https://arxiv.org/pdf/2401.54321",
    }
    client.post(
        "/api/projects/Labit/papers/import/arxiv",
        data={"metadata": json.dumps(first_metadata)},
        files={"pdf": ("paper.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    client.post(
        "/api/projects/Labit/papers/import/arxiv",
        data={"metadata": json.dumps(second_metadata)},
        files={"pdf": ("paper.pdf", b"%PDF-1.7\n", "application/pdf")},
    )

    reject_response = client.put(
        "/api/projects/Labit/papers/arxiv-2401.12345/status",
        json={"status": "rejected"},
    )
    default_response = client.get("/api/projects/Labit/papers")
    included_response = client.get("/api/projects/Labit/papers?include_rejected=true")

    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"
    assert default_response.status_code == 200
    assert [item["title"] for item in default_response.json()] == ["Useful Paper"]
    assert included_response.status_code == 200
    assert {item["title"] for item in included_response.json()} == {"Rejected Paper", "Useful Paper"}


def test_api_backfills_project_paper_submitted_dates(tmp_path: Path, monkeypatch) -> None:
    paths = _create_project(tmp_path)

    def fake_fetch(self, arxiv_id: str) -> dict[str, object]:
        assert arxiv_id == "2401.12345"
        return {"submitted_date": "2024-01-02"}

    monkeypatch.setattr("labit.papers.service.PaperService._fetch_arxiv_metadata", fake_fetch)
    client = TestClient(create_app(paths))
    metadata = {
        "arxiv_id": "2401.12345",
        "title": "A Useful Paper",
        "authors": ["Ada Lovelace"],
        "abstract": "A compact abstract.",
        "url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
    }
    client.post(
        "/api/projects/Labit/papers/import/arxiv",
        data={"metadata": json.dumps(metadata)},
        files={"pdf": ("paper.pdf", b"%PDF-1.7\n", "application/pdf")},
    )

    response = client.post("/api/projects/Labit/papers/backfill-dates")

    assert response.status_code == 200
    assert response.json() == {"updated": 1}
    metadata_path = tmp_path / "vault" / "projects" / "Labit" / "papers" / "arxiv-2401.12345" / "paper.yaml"
    assert yaml.safe_load(metadata_path.read_text(encoding="utf-8"))["submitted_date"] == "2024-01-02"


def test_api_updates_project_paper_note(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    metadata = {
        "arxiv_id": "2401.12345",
        "title": "A Useful Paper",
        "authors": ["Ada Lovelace"],
        "abstract": "A compact abstract.",
        "url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
    }
    client.post(
        "/api/projects/Labit/papers/import/arxiv",
        data={"metadata": json.dumps(metadata)},
        files={"pdf": ("paper.pdf", b"%PDF-1.7\n", "application/pdf")},
    )

    content = "# Reading note\n\nInline $x^2$ and a list."
    save_response = client.put(
        "/api/projects/Labit/papers/arxiv-2401.12345/note",
        json={"content": content},
    )
    get_response = client.get("/api/projects/Labit/papers/2401.12345/note")

    assert save_response.status_code == 200
    assert save_response.json() == {"content": content}
    assert get_response.status_code == 200
    assert get_response.json() == {"content": content}
    notes_path = tmp_path / "vault" / "projects" / "Labit" / "papers" / "arxiv-2401.12345" / "artifacts" / "notes.md"
    assert notes_path.read_text(encoding="utf-8") == content


def test_api_serves_pdf_byte_ranges(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    metadata = {
        "arxiv_id": "2401.12345",
        "title": "A Useful Paper",
        "authors": ["Ada Lovelace"],
        "abstract": "A compact abstract.",
        "url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
    }
    client.post(
        "/api/projects/Labit/papers/import/arxiv",
        data={"metadata": json.dumps(metadata)},
        files={"pdf": ("paper.pdf", b"0123456789", "application/pdf")},
    )

    response = client.get(
        "/api/projects/Labit/papers/arxiv-2401.12345/pdf",
        headers={"Origin": "http://127.0.0.1:5173", "Range": "bytes=2-5"},
    )

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "Content-Range" in response.headers["access-control-expose-headers"]
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "public, max-age=86400, immutable"
    assert response.headers["content-length"] == "4"
    assert response.headers["content-range"] == "bytes 2-5/10"


def test_api_serves_pdf_suffix_byte_ranges(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    metadata = {
        "arxiv_id": "2401.12345",
        "title": "A Useful Paper",
        "authors": ["Ada Lovelace"],
        "abstract": "A compact abstract.",
        "url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
    }
    client.post(
        "/api/projects/Labit/papers/import/arxiv",
        data={"metadata": json.dumps(metadata)},
        files={"pdf": ("paper.pdf", b"0123456789", "application/pdf")},
    )

    response = client.get(
        "/api/projects/Labit/papers/arxiv-2401.12345/pdf",
        headers={"Range": "bytes=-4"},
    )

    assert response.status_code == 206
    assert response.content == b"6789"
    assert response.headers["content-length"] == "4"
    assert response.headers["content-range"] == "bytes 6-9/10"


def _make_test_pdf() -> bytes:
    """Create a minimal 2-page PDF via PyMuPDF for render tests."""
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "Test PDF page 1", fontsize=24)
    page2 = doc.new_page(width=612, height=792)
    page2.insert_text((72, 72), "Test PDF page 2", fontsize=24)
    data = doc.tobytes()
    doc.close()
    return data


def test_api_reader_manifest_and_renders(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))
    metadata = {
        "arxiv_id": "2401.12345",
        "title": "A Useful Paper",
        "authors": ["Ada Lovelace"],
        "abstract": "A compact abstract.",
        "url": "https://arxiv.org/abs/2401.12345",
        "pdf_url": "https://arxiv.org/pdf/2401.12345",
    }
    client.post(
        "/api/projects/Labit/papers/import/arxiv",
        data={"metadata": json.dumps(metadata)},
        files={"pdf": ("paper.pdf", _make_test_pdf(), "application/pdf")},
    )

    # Reader manifest should be available
    manifest_response = client.get("/api/projects/Labit/papers/arxiv-2401.12345/reader-manifest")
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["page_count"] == 2
    assert len(manifest["pages"]) == 1  # only page 1 pre-rendered
    p1 = manifest["pages"][0]
    assert p1["page"] == 1
    assert "first_viewport_tile" in p1
    assert "retina" in p1

    # Render files should be servable
    render_response = client.get(
        f"/api/projects/Labit/papers/arxiv-2401.12345/renders/{p1['retina']}"
    )
    assert render_response.status_code == 200
    assert render_response.headers["cache-control"] == "public, max-age=31536000, immutable"

    # On-demand page render
    page2_response = client.get(
        "/api/projects/Labit/papers/arxiv-2401.12345/pages/2/image?w=1600"
    )
    assert page2_response.status_code == 200


def test_api_returns_project_list(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))

    response = client.get("/api/projects")

    assert response.status_code == 200
    assert response.json() == {"projects": ["Labit"], "active_project": "Labit"}


def test_api_creates_project_and_sets_active(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))

    response = client.post(
        "/api/projects",
        json={"name": "InboxPilot", "description": "Email triage experiments"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "InboxPilot"
    assert payload["set_active"] is True
    assert (paths.project_configs_dir / "InboxPilot.yaml").exists()
    assert (paths.vault_projects_dir / "InboxPilot" / "code").is_dir()
    assert (paths.vault_projects_dir / "InboxPilot" / "docs").is_dir()
    assert paths.active_project_path.read_text(encoding="utf-8").strip() == "InboxPilot"

    list_response = client.get("/api/projects")
    assert list_response.status_code == 200
    assert list_response.json() == {
        "projects": ["InboxPilot", "Labit"],
        "active_project": "InboxPilot",
    }


def test_api_create_project_persists_cli_project_fields(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))

    response = client.post(
        "/api/projects",
        json={
            "name": "InboxPilot",
            "description": "Email triage experiments",
            "repo": "git@github.com:qinglinh2003/InboxPilot.git",
            "keywords": ["email", "triage", "automation"],
            "relevance_criteria": "Inbox automation and prioritization experiments",
        },
    )

    assert response.status_code == 201
    config = yaml.safe_load((paths.project_configs_dir / "InboxPilot.yaml").read_text(encoding="utf-8"))
    assert config["name"] == "InboxPilot"
    assert config["description"] == "Email triage experiments"
    assert config["repo"] == "git@github.com:qinglinh2003/InboxPilot.git"
    assert config["keywords"] == ["email", "triage", "automation"]
    assert config["relevance_criteria"] == "Inbox automation and prioritization experiments"


def test_api_create_project_rejects_duplicate(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))

    response = client.post("/api/projects", json={"name": "Labit"})

    assert response.status_code == 409


def test_api_serves_frontend_dist_from_code_directory(tmp_path: Path, monkeypatch) -> None:
    paths = _create_project(tmp_path)
    frontend_dist = tmp_path / "frontend-dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text("<main>Labit UI</main>", encoding="utf-8")
    monkeypatch.setenv("LABIT_FRONTEND_DIST", str(frontend_dist))
    client = TestClient(create_app(paths))

    response = client.get("/")

    assert response.status_code == 200
    assert "Labit UI" in response.text


def test_api_cors_allows_local_frontend_and_chrome_extension(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))

    local_response = client.options(
        "/api/projects",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert local_response.status_code == 200
    assert local_response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"

    extension_response = client.options(
        "/api/projects",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert extension_response.status_code == 200
    assert (
        extension_response.headers["access-control-allow-origin"]
        == "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    )


def test_api_cors_rejects_unlisted_web_origins(tmp_path: Path) -> None:
    paths = _create_project(tmp_path)
    client = TestClient(create_app(paths))

    response = client.options(
        "/api/projects",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
