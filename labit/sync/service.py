from __future__ import annotations

import json
import shlex
import subprocess

from labit.models import ComputeProfile, ProjectSpec, StorageProfile
from labit.paths import RepoPaths
from labit.services.compute_service import ComputeService
from labit.services.project_service import ProjectService
from labit.services.storage_service import StorageService
from labit.sync.models import SyncDirection, SyncSize, SyncStatusEntry, SyncTransferEntry


class SyncService:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        project_service: ProjectService | None = None,
        compute_service: ComputeService | None = None,
        storage_service: StorageService | None = None,
    ):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)
        self.compute_service = compute_service or ComputeService(paths)
        self.storage_service = storage_service or StorageService(paths, compute_service=self.compute_service)

    def status(self, project: str) -> list[SyncStatusEntry]:
        spec, compute, storage = self._load_context(project)
        ssh_command = self.compute_service.build_ssh_command(compute)
        entries: list[SyncStatusEntry] = []
        for dir_name in spec.sync_dirs:
            compute_path = self._compute_path(compute, dir_name)
            remote_path = self.storage_service.build_remote_uri(storage, project=spec.name, dir_name=dir_name)
            compute_size = self._compute_dir_status(ssh_command, compute_path)
            remote_size = self._remote_dir_status(ssh_command, remote_path)
            entries.append(
                SyncStatusEntry(
                    dir_name=dir_name,
                    compute_path=compute_path,
                    remote_path=remote_path,
                    compute=compute_size,
                    remote=remote_size,
                )
            )
        return entries

    def push(self, project: str) -> list[SyncTransferEntry]:
        spec, compute, storage = self._load_context(project)
        ssh_command = self.compute_service.build_ssh_command(compute)
        return [
            self._transfer_one(
                ssh_command,
                dir_name=dir_name,
                direction=SyncDirection.PUSH,
                compute_path=self._compute_path(compute, dir_name),
                remote_path=self.storage_service.build_remote_uri(storage, project=spec.name, dir_name=dir_name),
            )
            for dir_name in spec.sync_dirs
        ]

    def pull(self, project: str) -> list[SyncTransferEntry]:
        spec, compute, storage = self._load_context(project)
        ssh_command = self.compute_service.build_ssh_command(compute)
        return [
            self._transfer_one(
                ssh_command,
                dir_name=dir_name,
                direction=SyncDirection.PULL,
                compute_path=self._compute_path(compute, dir_name),
                remote_path=self.storage_service.build_remote_uri(storage, project=spec.name, dir_name=dir_name),
            )
            for dir_name in spec.sync_dirs
        ]

    def _load_context(self, project: str) -> tuple[ProjectSpec, ComputeProfile, StorageProfile]:
        spec = self.project_service.load_project(project)
        if not spec.sync_dirs:
            raise ValueError(
                f"Project '{spec.name}' does not define any sync directories. Add 'sync_dirs' in the project config first."
            )
        compute = self.compute_service.load_compute(spec.compute_profile)
        storage = self.storage_service.load_storage(spec.storage_profile)
        return spec, compute, storage

    def _compute_path(self, compute: ComputeProfile, dir_name: str) -> str:
        base = compute.workspace.workdir.rstrip("/") or "."
        return f"{base}/{dir_name}"

    def _compute_dir_status(self, ssh_command: list[str], compute_path: str) -> SyncSize:
        script = f"""{self._remote_env_preamble()}set -euo pipefail
TARGET={shlex.quote(compute_path)}
resolve_path() {{
  local value="$1"
  case "$value" in
    "~") value="$HOME" ;;
    "~"/*) value="$HOME/${{value#~/}}" ;;
  esac
  printf '%s\\n' "$value"
}}
TARGET="$(resolve_path "$TARGET")"
if [ ! -d "$TARGET" ]; then
  printf '%s\\n' '{{"error": "missing or not accessible"}}'
  exit 0
fi
BYTES=$(du -sb "$TARGET" 2>/dev/null | awk '{{print $1}}')
COUNT=$(find "$TARGET" -type f 2>/dev/null | wc -l | tr -d ' ')
printf '{{"bytes": %s, "count": %s}}\\n' "$BYTES" "$COUNT"
"""
        result = self._ssh_run(ssh_command, script, timeout=45)
        if result.returncode != 0:
            return SyncSize(error=(result.stderr or result.stdout or "Could not inspect compute directory.").strip())
        try:
            payload = json.loads((result.stdout or "").strip() or "{}")
        except json.JSONDecodeError:
            return SyncSize(error=(result.stdout or result.stderr or "Could not parse compute status.").strip())
        if payload.get("error"):
            return SyncSize(error=str(payload["error"]))
        return SyncSize(bytes=payload.get("bytes"), count=payload.get("count"))

    def _remote_dir_status(self, ssh_command: list[str], remote_path: str) -> SyncSize:
        script = f"""{self._remote_env_preamble()}set -euo pipefail
TARGET={shlex.quote(remote_path)}
if ! command -v rclone >/dev/null 2>&1; then
  printf '%s\\n' '{{"error": "rclone not installed"}}'
  exit 0
fi
if ! rclone lsf "$TARGET" >/dev/null 2>&1; then
  printf '%s\\n' '{{"error": "missing or not accessible"}}'
  exit 0
fi
rclone size "$TARGET" --json
"""
        result = self._ssh_run(ssh_command, script, timeout=60)
        if result.returncode != 0:
            return SyncSize(error=(result.stderr or result.stdout or "Could not inspect remote storage.").strip())
        try:
            payload = json.loads((result.stdout or "").strip() or "{}")
        except json.JSONDecodeError:
            return SyncSize(error=(result.stdout or result.stderr or "Could not parse storage status.").strip())
        if payload.get("error"):
            return SyncSize(error=str(payload["error"]))
        return SyncSize(bytes=payload.get("bytes"), count=payload.get("count"))

    def _transfer_one(
        self,
        ssh_command: list[str],
        *,
        dir_name: str,
        direction: SyncDirection,
        compute_path: str,
        remote_path: str,
    ) -> SyncTransferEntry:
        source = compute_path if direction is SyncDirection.PUSH else remote_path
        destination = remote_path if direction is SyncDirection.PUSH else compute_path
        script = f"""{self._remote_env_preamble()}set -euo pipefail
SRC={shlex.quote(source)}
DST={shlex.quote(destination)}
resolve_path() {{
  local value="$1"
  case "$value" in
    "~") value="$HOME" ;;
    "~"/*) value="$HOME/${{value#~/}}" ;;
  esac
  printf '%s\\n' "$value"
}}
if [[ "$SRC" != *:* ]]; then
  SRC="$(resolve_path "$SRC")"
fi
if [[ "$DST" != *:* ]]; then
  DST="$(resolve_path "$DST")"
  mkdir -p "$DST"
fi
rclone copy "$SRC" "$DST"
"""
        try:
            result = self._ssh_run(ssh_command, script, timeout=3600)
        except subprocess.TimeoutExpired as exc:
            return SyncTransferEntry(
                dir_name=dir_name,
                direction=direction,
                compute_path=compute_path,
                remote_path=remote_path,
                ok=False,
                exit_code=None,
                stdout_tail=self._tail(exc.stdout or ""),
                stderr_tail=self._tail(exc.stderr or "Sync timed out."),
            )
        return SyncTransferEntry(
            dir_name=dir_name,
            direction=direction,
            compute_path=compute_path,
            remote_path=remote_path,
            ok=result.returncode == 0,
            exit_code=result.returncode,
            stdout_tail=self._tail(result.stdout or ""),
            stderr_tail=self._tail(result.stderr or ""),
        )

    def _ssh_run(self, ssh_command: list[str], script: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*ssh_command, "bash", "-s"],
            input=script,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    def _tail(self, value: str, *, limit: int = 800) -> str:
        text = value.strip()
        if len(text) <= limit:
            return text
        return text[-limit:]

    def _remote_env_preamble(self) -> str:
        return 'export PATH="$HOME/.local/bin:$HOME/bin:$PATH"\n'
