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
