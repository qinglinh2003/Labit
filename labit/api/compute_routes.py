from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from labit.services.compute_service import ComputeService


class ComputeProfileResponse(BaseModel):
    name: str
    user: str
    host: str
    port: int
    identity_file: str | None
    workdir: str
    notes: str
    ssh_display: str


class SaveProfileRequest(BaseModel):
    user: str
    host: str
    port: int = 22
    identity_file: str | None = None
    workdir: str = ""
    notes: str = ""


class TestResult(BaseModel):
    success: bool
    message: str


def _to_response(profile) -> ComputeProfileResponse:
    return ComputeProfileResponse(
        name=profile.name,
        user=profile.connection.user,
        host=profile.connection.host,
        port=profile.connection.port,
        identity_file=profile.connection.identity_file,
        workdir=profile.workdir,
        notes=profile.notes,
        ssh_display=profile.ssh_display(),
    )


def mount_compute_routes(svc: ComputeService) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project}/compute", tags=["compute"])

    @router.get("", response_model=list[ComputeProfileResponse])
    def list_profiles(project: str) -> list[ComputeProfileResponse]:
        try:
            return [_to_response(p) for p in svc.list_profiles(project)]
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/{name}", response_model=ComputeProfileResponse)
    def get_profile(project: str, name: str) -> ComputeProfileResponse:
        try:
            return _to_response(svc.get_profile(project, name))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put("/{name}", response_model=ComputeProfileResponse)
    def save_profile(project: str, name: str, body: SaveProfileRequest) -> ComputeProfileResponse:
        try:
            profile = svc.build_profile(
                name=name,
                user=body.user,
                host=body.host,
                port=body.port,
                identity_file=body.identity_file,
                workdir=body.workdir,
                notes=body.notes,
            )
            svc.save_profile(project, profile)
            return _to_response(profile)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete("/{name}")
    def delete_profile(project: str, name: str) -> dict[str, str]:
        try:
            svc.delete_profile(project, name)
            return {"status": "deleted"}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/{name}/test", response_model=TestResult)
    def test_profile(project: str, name: str) -> TestResult:
        try:
            result = svc.test_profile(project, name)
            if result.returncode == 0 and "labit-ssh-ok" in result.stdout:
                return TestResult(success=True, message="Connected successfully")
            return TestResult(
                success=False,
                message=result.stderr.strip() or result.stdout.strip() or "Connection failed",
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            return TestResult(success=False, message=str(exc))

    return router
