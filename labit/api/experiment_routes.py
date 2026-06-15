"""REST API routes for the experiment module."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from labit.services.experiment_service import ExperimentService


# ── request / response models ────────────────────────────────────────

class ExperimentResponse(BaseModel):
    experiment_id: str
    name: str
    description: str
    profile: str
    tags: list[str]
    has_run_sh: bool = True


class RunResponse(BaseModel):
    run_id: str
    experiment_id: str
    name: str
    profile: str
    script: str
    command: str
    status: str
    pid: int | None
    exit_code: int | None
    remote_workdir: str
    remote_run_dir: str
    local_commit: str
    dirty: bool
    created_at: str
    started_at: str
    finished_at: str
    notes: str
    error: str
    sync: dict | None = None


class ExperimentWithLatestRun(BaseModel):
    experiment: ExperimentResponse
    latest_run: RunResponse | None


class LaunchRequest(BaseModel):
    profile: str | None = None
    notes: str = ""


class UpdateStatusRequest(BaseModel):
    status: str  # "completed" | "failed" | "stopped"
    exit_code: int | None = None


class LogsResponse(BaseModel):
    content: str
    stream: str
    tail: int


class EventsResponse(BaseModel):
    events: list[dict]
    plain_lines: int
    total_lines: int


class MetricsResponse(BaseModel):
    metrics: dict[str, list[dict]]
    steps: list[int]


class SyncRequest(BaseModel):
    mode: str = "logs"  # logs | results | all
    exclude_patterns: list[str] = []


class SyncResponse(BaseModel):
    logs_synced_at: str
    results_synced_at: str = ""
    last_sync_status: str
    last_sync_error: str
    files_synced: int
    bytes_synced: int
    excluded_patterns: list[str] = []


class ResultEntry(BaseModel):
    path: str
    size: int
    modified: str


# ── router factory ───────────────────────────────────────────────────

def mount_experiment_routes(svc: ExperimentService) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project}/experiments", tags=["experiments"])

    @router.get("", response_model=list[ExperimentWithLatestRun])
    def list_experiments(project: str) -> list[ExperimentWithLatestRun]:
        try:
            experiments = svc.list_experiments(project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        result: list[ExperimentWithLatestRun] = []
        for exp in experiments:
            latest = svc.latest_run(project, exp.experiment_id)
            result.append(
                ExperimentWithLatestRun(
                    experiment=ExperimentResponse(
                        experiment_id=exp.experiment_id,
                        name=exp.name,
                        description=exp.description,
                        profile=exp.profile,
                        tags=exp.tags,
                    ),
                    latest_run=_run_to_response(latest) if latest else None,
                )
            )
        return result

    @router.get("/{experiment_id}", response_model=ExperimentWithLatestRun)
    def get_experiment(project: str, experiment_id: str) -> ExperimentWithLatestRun:
        try:
            exp = svc.get_experiment(project, experiment_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        latest = svc.latest_run(project, experiment_id)
        return ExperimentWithLatestRun(
            experiment=ExperimentResponse(
                experiment_id=exp.experiment_id,
                name=exp.name,
                description=exp.description,
                profile=exp.profile,
                tags=exp.tags,
            ),
            latest_run=_run_to_response(latest) if latest else None,
        )

    @router.get("/{experiment_id}/runs", response_model=list[RunResponse])
    def list_runs(project: str, experiment_id: str) -> list[RunResponse]:
        return [_run_to_response(r) for r in svc.list_runs(project, experiment_id)]

    @router.post("/{experiment_id}/launch", response_model=RunResponse)
    def launch_experiment(project: str, experiment_id: str, body: LaunchRequest) -> RunResponse:
        try:
            record = svc.launch(
                project, experiment_id,
                profile_override=body.profile,
                notes=body.notes,
            )
            return _run_to_response(record)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/{experiment_id}/runs/{run_id}/refresh", response_model=RunResponse)
    def refresh_run(project: str, experiment_id: str, run_id: str) -> RunResponse:
        try:
            record = svc.refresh_status(project, experiment_id, run_id)
            return _run_to_response(record)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/{experiment_id}/runs/{run_id}/logs", response_model=LogsResponse)
    def get_logs(
        project: str,
        experiment_id: str,
        run_id: str,
        stream: str = Query("stdout", pattern="^(stdout|stderr)$"),
        tail: int = Query(200, ge=1, le=10000),
    ) -> LogsResponse:
        try:
            content = svc.tail_logs(
                project, experiment_id, run_id, stream=stream, tail=tail,
            )
            return LogsResponse(content=content, stream=stream, tail=tail)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/{experiment_id}/runs/{run_id}/stop", response_model=RunResponse)
    def stop_run(project: str, experiment_id: str, run_id: str) -> RunResponse:
        try:
            record = svc.stop_run(project, experiment_id, run_id)
            return _run_to_response(record)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/{experiment_id}/runs/{run_id}/status", response_model=RunResponse)
    def update_run_status(
        project: str, experiment_id: str, run_id: str, body: UpdateStatusRequest,
    ) -> RunResponse:
        """Manually override a run's status (e.g. when remote is unreachable)."""
        allowed = {"completed", "failed", "stopped"}
        if body.status not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"status must be one of {allowed}",
            )
        try:
            record = svc.update_status(
                project, experiment_id, run_id,
                status=body.status, exit_code=body.exit_code,
            )
            return _run_to_response(record)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/{experiment_id}/runs/{run_id}/events",
        response_model=EventsResponse,
    )
    def get_events(project: str, experiment_id: str, run_id: str) -> EventsResponse:
        try:
            data = svc.fetch_events(project, experiment_id, run_id)
            return EventsResponse(**data)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/{experiment_id}/runs/{run_id}/metrics",
        response_model=MetricsResponse,
    )
    def get_metrics(project: str, experiment_id: str, run_id: str) -> MetricsResponse:
        try:
            data = svc.fetch_metrics(project, experiment_id, run_id)
            return MetricsResponse(**data)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/{experiment_id}/runs/{run_id}/sync",
        response_model=SyncResponse,
    )
    def sync_run(
        project: str, experiment_id: str, run_id: str, body: SyncRequest | None = None,
    ) -> SyncResponse:
        if body is None:
            body = SyncRequest()
        allowed_modes = {"logs", "results", "all"}
        if body.mode not in allowed_modes:
            raise HTTPException(
                status_code=422,
                detail=f"mode must be one of {allowed_modes}",
            )
        try:
            if body.mode == "logs":
                manifest = svc.sync_logs(project, experiment_id, run_id)
            elif body.mode == "results":
                manifest = svc.sync_results(
                    project, experiment_id, run_id,
                    exclude_patterns=body.exclude_patterns or None,
                )
            else:  # all
                manifest = svc.sync_all(
                    project, experiment_id, run_id,
                    exclude_patterns=body.exclude_patterns or None,
                )
            return SyncResponse(
                logs_synced_at=manifest.logs_synced_at,
                results_synced_at=manifest.results_synced_at,
                last_sync_status=manifest.last_sync_status,
                last_sync_error=manifest.last_sync_error,
                files_synced=manifest.files_synced,
                bytes_synced=manifest.bytes_synced,
                excluded_patterns=manifest.excluded_patterns,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/{experiment_id}/runs/{run_id}/results",
        response_model=list[ResultEntry],
    )
    def list_results(
        project: str, experiment_id: str, run_id: str,
    ) -> list[ResultEntry]:
        try:
            entries = svc.list_results(project, experiment_id, run_id)
            return [ResultEntry(**e) for e in entries]
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/{experiment_id}/runs/{run_id}/results/{file_path:path}/preview")
    def preview_result_file(
        project: str, experiment_id: str, run_id: str, file_path: str,
    ):
        try:
            return svc.preview_result_file(
                project, experiment_id, run_id, file_path,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/{experiment_id}/runs/{run_id}/results/{file_path:path}")
    def get_result_file(project: str, experiment_id: str, run_id: str, file_path: str):
        from fastapi.responses import FileResponse

        try:
            resolved = svc.read_result_file(
                project, experiment_id, run_id, file_path,
            )
            return FileResponse(resolved)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router


def _run_to_response(record) -> RunResponse:
    data = record.to_dict()
    data["error"] = data.get("error") or ""
    return RunResponse(**data)
