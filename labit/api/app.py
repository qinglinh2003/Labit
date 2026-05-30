from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from labit.papers.models import ArxivPaperMetadata, PaperRecord
from labit.papers.service import PaperService
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService

DEFAULT_CORS_ORIGINS = [
    "http://127.0.0.1:4173",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8787",
    "http://localhost:4173",
    "http://localhost:5173",
    "http://localhost:8787",
]
CHROME_EXTENSION_ORIGIN_RE = r"^chrome-extension://[a-z]{32}$"
PDF_CACHE_CONTROL = "public, max-age=86400, immutable"
PDF_RANGE_CHUNK_SIZE = 1024 * 1024


class ProjectListResponse(BaseModel):
    projects: list[str]
    active_project: str | None = None


class ArtifactRecord(BaseModel):
    name: str
    path: str
    relative_path: str
    size_bytes: int


def create_app(paths: RepoPaths | None = None) -> FastAPI:
    repo_paths = paths or RepoPaths.discover()
    project_service = ProjectService(repo_paths)
    paper_service = PaperService(repo_paths, project_service=project_service)

    app = FastAPI(title="LABIT Research OS API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_origin_regex=CHROME_EXTENSION_ORIGIN_RE,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Accept-Ranges", "Cache-Control", "Content-Length", "Content-Range"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/projects", response_model=ProjectListResponse)
    def list_projects() -> ProjectListResponse:
        return ProjectListResponse(
            projects=project_service.list_project_names(),
            active_project=project_service.active_project_name(),
        )

    @app.get("/api/projects/{project}/papers", response_model=list[PaperRecord])
    def list_papers(project: str) -> list[PaperRecord]:
        try:
            return paper_service.list_papers(project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{project}/papers/{paper_id}", response_model=PaperRecord)
    def get_paper(project: str, paper_id: str) -> PaperRecord:
        try:
            return paper_service.get_paper(project=project, paper_id=paper_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/{project}/papers/import/arxiv", response_model=PaperRecord)
    async def import_arxiv_paper(
        project: str,
        metadata: str = Form(...),
        pdf: UploadFile = File(...),
    ) -> PaperRecord:
        try:
            raw_metadata = json.loads(metadata)
            parsed_metadata = ArxivPaperMetadata.model_validate(raw_metadata)
            pdf_content = await pdf.read()
            return paper_service.import_arxiv_pdf(
                project=project,
                metadata=parsed_metadata,
                pdf_content=pdf_content,
            )
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="metadata must be valid JSON") from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project}/papers/{paper_id}/pdf")
    def get_paper_pdf(project: str, paper_id: str, request: Request):
        try:
            pdf_path = paper_service.pdf_path(project=project, paper_id=paper_id)
            range_header = request.headers.get("range")
            if range_header:
                return _range_pdf_response(pdf_path, range_header)
            return FileResponse(
                pdf_path,
                media_type="application/pdf",
                filename=pdf_path.name,
                content_disposition_type="inline",
                headers={
                    "Accept-Ranges": "bytes",
                    "Cache-Control": PDF_CACHE_CONTROL,
                },
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project}/papers/{paper_id}/artifacts", response_model=list[ArtifactRecord])
    def list_artifacts(project: str, paper_id: str) -> list[ArtifactRecord]:
        try:
            artifacts = paper_service.list_artifacts(project=project, paper_id=paper_id)
            return [ArtifactRecord.model_validate(item) for item in artifacts]
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    _mount_frontend(app, _frontend_dist_dir())
    return app


def _frontend_dist_dir() -> Path:
    override = os.environ.get("LABIT_FRONTEND_DIST")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "labit-ui" / "dist"


def _mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    if not dist_dir.exists():
        return
    app.mount("/", StaticFiles(directory=dist_dir, html=True), name="frontend")


def _cors_origins() -> list[str]:
    raw = os.environ.get("LABIT_API_CORS_ORIGINS", "")
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or DEFAULT_CORS_ORIGINS


def _range_pdf_response(pdf_path: Path, range_header: str) -> StreamingResponse:
    file_size = pdf_path.stat().st_size
    start, end = _parse_range_header(range_header, file_size)
    content_length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": PDF_CACHE_CONTROL,
        "Content-Disposition": f'inline; filename="{pdf_path.name}"',
        "Content-Length": str(content_length),
        "Content-Range": f"bytes {start}-{end}/{file_size}",
    }
    return StreamingResponse(
        _iter_file_range(pdf_path, start, end),
        status_code=206,
        media_type="application/pdf",
        headers=headers,
    )


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    unit, _, raw_range = range_header.partition("=")
    if unit.strip().lower() != "bytes" or not raw_range:
        raise HTTPException(status_code=416, detail="Invalid range header")

    first_range = raw_range.split(",", 1)[0].strip()
    raw_start, separator, raw_end = first_range.partition("-")
    if separator != "-":
        raise HTTPException(status_code=416, detail="Invalid range header")

    try:
        if raw_start == "":
            suffix_length = int(raw_end)
            if suffix_length <= 0:
                raise ValueError
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
        else:
            start = int(raw_start)
            end = int(raw_end) if raw_end else file_size - 1
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="Invalid range header") from exc

    if start < 0 or end < start or start >= file_size:
        raise HTTPException(status_code=416, detail="Requested range not satisfiable")
    return start, min(end, file_size - 1)


def _iter_file_range(pdf_path: Path, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with pdf_path.open("rb") as file:
        file.seek(start)
        while remaining > 0:
            chunk = file.read(min(PDF_RANGE_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
