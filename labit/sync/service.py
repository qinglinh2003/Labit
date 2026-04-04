from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import PurePosixPath

from labit.models import ComputeBackend
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService
from labit.sync.models import SyncDirection, SyncSize, SyncStatusEntry, SyncTransferEntry


class SyncService:
    DEFAULT_RCLONE_REMOTE = "r2"
    DEFAULT_BUCKET = "research-data"

    def __init__(self, paths: RepoPaths, *, project_service: ProjectService | None = None):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)

    def status(self, project: str) -> list[SyncStatusEntry]:
        spec = self._load_sync_project(project)
        entries: list[SyncStatusEntry] = []
        for dir_name in spec.sync_dirs:
            compute_path = self._compute_path(spec.compute.workdir or "", dir_name)
            remote_path = self._remote_path(spec.name, dir_name)
            entries.append(
                SyncStatusEntry(
                    dir_name=dir_name,
                    compute_path=compute_path,
                    remote_path=remote_path,
                    compute=self._ssh_rclone_size(spec.compute.host or "", compute_path),
                    remote=self._local_rclone_size(remote_path),
                )
            )
        return entries

    def push(self, project: str) -> list[SyncTransferEntry]:
        return self._copy(project=project, direction=SyncDirection.PUSH)

    def pull(self, project: str) -> list[SyncTransferEntry]:
        return self._copy(project=project, direction=SyncDirection.PULL)

    def rclone_remote(self) -> str:
        return os.environ.get("LABIT_RCLONE_REMOTE", self.DEFAULT_RCLONE_REMOTE).strip() or self.DEFAULT_RCLONE_REMOTE

    def bucket_name(self) -> str:
        return os.environ.get("LABIT_RCLONE_BUCKET", self.DEFAULT_BUCKET).strip() or self.DEFAULT_BUCKET

    def _copy(self, *, project: str, direction: SyncDirection) -> list[SyncTransferEntry]:
        spec = self._load_sync_project(project)
        entries: list[SyncTransferEntry] = []
        for dir_name in spec.sync_dirs:
            compute_path = self._compute_path(spec.compute.workdir or "", dir_name)
            remote_path = self._remote_path(spec.name, dir_name)
            if direction == SyncDirection.PUSH:
                source = compute_path
                destination = remote_path
            else:
                source = remote_path
                destination = compute_path
            entries.append(
                self._ssh_rclone_copy(
                    host=spec.compute.host or "",
                    direction=direction,
                    dir_name=dir_name,
                    source=source,
                    destination=destination,
                    compute_path=compute_path,
                    remote_path=remote_path,
                )
            )
        return entries

    def _load_sync_project(self, project: str):
        resolved = self.project_service.resolve_project_name(project)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{project}' not found. Available projects: {', '.join(self.project_service.list_project_names()) or '(none)'}"
            )
        spec = self.project_service.load_project(resolved)
        if spec.compute.backend != ComputeBackend.SSH:
            raise ValueError(
                f"Project '{resolved}' does not declare an SSH compute backend. Sync v1 requires compute.backend=ssh."
            )
        if not spec.compute.host or not spec.compute.workdir:
            raise ValueError(
                f"Project '{resolved}' is missing compute.host or compute.workdir, which are required for sync."
            )
        if not spec.sync_dirs:
            raise ValueError(f"Project '{resolved}' does not declare any sync_dirs.")
        return spec

    def _compute_path(self, workdir: str, dir_name: str) -> str:
        return str(PurePosixPath(workdir.rstrip("/")) / dir_name)

    def _remote_path(self, project: str, dir_name: str) -> str:
        return f"{self.rclone_remote()}:{self.bucket_name()}/{project}/{dir_name}"

    def _local_rclone_size(self, target: str) -> SyncSize:
        try:
            result = subprocess.run(
                ["rclone", "size", target, "--json"],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return SyncSize(error="rclone is not installed or not available in PATH.")
        except subprocess.TimeoutExpired:
            return SyncSize(error="local rclone size timed out.")
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "rclone size failed").strip()
            return SyncSize(error=message[-800:])
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return SyncSize(error="Failed to parse `rclone size --json` output.")
        return SyncSize(
            bytes=self._coerce_int(payload.get("bytes")),
            count=self._coerce_int(payload.get("count")),
        )

    def _ssh_rclone_size(self, host: str, target: str) -> SyncSize:
        command = f"rclone size {shlex.quote(target)} --json"
        try:
            result = subprocess.run(
                ["ssh", host, "bash", "-lc", command],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return SyncSize(error="ssh is not installed or not available in PATH.")
        except subprocess.TimeoutExpired:
            return SyncSize(error="remote rclone size timed out.")
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "remote rclone size failed").strip()
            return SyncSize(error=message[-800:])
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return SyncSize(error="Failed to parse remote `rclone size --json` output.")
        return SyncSize(
            bytes=self._coerce_int(payload.get("bytes")),
            count=self._coerce_int(payload.get("count")),
        )

    def _ssh_rclone_copy(
        self,
        *,
        host: str,
        direction: SyncDirection,
        dir_name: str,
        source: str,
        destination: str,
        compute_path: str,
        remote_path: str,
    ) -> SyncTransferEntry:
        command = (
            f"rclone copy {shlex.quote(source)} {shlex.quote(destination)} "
            "--update --transfers 8"
        )
        try:
            result = subprocess.run(
                ["ssh", host, "bash", "-lc", command],
                check=False,
                capture_output=True,
                text=True,
                timeout=3600,
            )
        except FileNotFoundError as exc:
            return SyncTransferEntry(
                dir_name=dir_name,
                direction=direction,
                compute_path=compute_path,
                remote_path=remote_path,
                ok=False,
                stderr_tail=str(exc),
            )
        except subprocess.TimeoutExpired:
            return SyncTransferEntry(
                dir_name=dir_name,
                direction=direction,
                compute_path=compute_path,
                remote_path=remote_path,
                ok=False,
                stderr_tail="remote rclone copy timed out.",
            )
        return SyncTransferEntry(
            dir_name=dir_name,
            direction=direction,
            compute_path=compute_path,
            remote_path=remote_path,
            ok=result.returncode == 0,
            exit_code=result.returncode,
            stdout_tail=(result.stdout or "").strip()[-1200:],
            stderr_tail=(result.stderr or "").strip()[-1200:],
        )

    def _coerce_int(self, value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
