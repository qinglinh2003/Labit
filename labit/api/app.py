from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
    def get_paper_pdf(project: str, paper_id: str) -> FileResponse:
        try:
            pdf_path = paper_service.pdf_path(project=project, paper_id=paper_id)
            return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)
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
