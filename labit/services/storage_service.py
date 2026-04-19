from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict

from labit.models import StorageProfile
from labit.paths import RepoPaths
from labit.services.compute_service import ComputeService


class StorageCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    config_ok: bool = False
    compute_ok: bool = False
    rclone_ok: bool = False
    remote_ok: bool = False
    bucket_ok: bool = False
    message: str = ""


class StorageService:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        compute_service: ComputeService | None = None,
    ):
        self.paths = paths
        self.compute_service = compute_service or ComputeService(paths)

    def list_storage_names(self) -> list[str]:
        if not self.paths.storage_configs_dir.exists():
            return []
        return sorted(path.stem for path in self.paths.storage_configs_dir.glob("*.yaml"))

    def resolve_storage_name(self, name: str) -> str | None:
        for candidate in self.list_storage_names():
            if candidate.lower() == name.lower():
                return candidate
        return None

    def load_storage(self, name: str) -> StorageProfile:
        resolved = self.resolve_storage_name(name)
        if resolved is None:
            raise FileNotFoundError(
                f"Storage profile '{name}' not found. Available profiles: {', '.join(self.list_storage_names()) or '(none)'}"
            )
        raw = yaml.safe_load((self.paths.storage_configs_dir / f"{resolved}.yaml").read_text()) or {}
        return StorageProfile.model_validate(raw)

    def save_storage(self, profile: StorageProfile, *, force: bool = False) -> dict:
        resolved = self.resolve_storage_name(profile.name)
        if resolved and not force:
            raise FileExistsError(
                f"Storage profile '{resolved}' already exists. Re-run with '--force' to overwrite."
            )
        self.paths.storage_configs_dir.mkdir(parents=True, exist_ok=True)
        config_path = self.paths.storage_configs_dir / f"{profile.name}.yaml"
        yaml_text = yaml.safe_dump(profile.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True)
        self._atomic_write(config_path, yaml_text)
        return {"name": profile.name, "config_path": str(config_path)}

    def delete_storage(self, name: str) -> dict:
        resolved = self.resolve_storage_name(name)
        if resolved is None:
            raise FileNotFoundError(
                f"Storage profile '{name}' not found. Available profiles: {', '.join(self.list_storage_names()) or '(none)'}"
            )
        path = self.paths.storage_configs_dir / f"{resolved}.yaml"
        if path.exists():
            path.unlink()
        return {"name": resolved, "config_path": str(path)}

    def build_remote_uri(self, profile: StorageProfile, *, project: str, dir_name: str) -> str:
        relative = profile.layout.path_template.format(project=project, dir=dir_name).strip("/")
        return f"{profile.rclone.remote}:{profile.rclone.bucket}/{relative}"

    def test_storage(
        self,
        name: str,
        *,
        compute_name: str | None = None,
        on_step: Callable[[str], None] | None = None,
    ) -> StorageCheckResult:
        profile = self.load_storage(name)
        result = StorageCheckResult(name=profile.name, config_ok=True, message="Storage profile is valid.")
        if compute_name is None:
            return result

        compute = self.compute_service.load_compute(compute_name)
        ssh_command = self.compute_service.build_ssh_command(compute)

        if on_step:
            on_step("Checking compute connection")
        probe = self._ssh_run(ssh_command, f"{self._remote_env_preamble()}printf 'LABIT_OK'\n", timeout=20)
        if probe.returncode != 0 or (probe.stdout or "").strip() != "LABIT_OK":
            return StorageCheckResult(
                name=profile.name,
                config_ok=True,
                message=(probe.stderr or probe.stdout or "Could not reach the compute host.").strip(),
            )
        result.compute_ok = True

        if on_step:
            on_step("Checking rclone")
        rclone_probe = self._ssh_run(
            ssh_command,
            f"{self._remote_env_preamble()}command -v rclone >/dev/null 2>&1 && printf 'OK'\n",
            timeout=20,
        )
        result.rclone_ok = rclone_probe.returncode == 0 and (rclone_probe.stdout or "").strip() == "OK"
        if not result.rclone_ok:
            result.message = "rclone is not installed on the compute host."
            return result

        if on_step:
            on_step("Checking rclone remote")
        remote_name = shlex.quote(profile.rclone.remote)
        remote_probe = self._ssh_run(
            ssh_command,
            f"{self._remote_env_preamble()}rclone listremotes | grep -Fx {remote_name}: >/dev/null && printf 'OK'\n",
            timeout=20,
        )
        result.remote_ok = remote_probe.returncode == 0 and (remote_probe.stdout or "").strip() == "OK"
        if not result.remote_ok:
            result.message = f"Remote '{profile.rclone.remote}' is not configured on the compute host."
            return result

        if on_step:
            on_step("Checking bucket access")
        bucket_uri = f"{profile.rclone.remote}:{profile.rclone.bucket}"
        bucket_probe = self._ssh_run(
            ssh_command,
            f"{self._remote_env_preamble()}rclone lsd {shlex.quote(bucket_uri)} >/dev/null 2>&1 && printf 'OK'\n",
            timeout=30,
        )
        result.bucket_ok = bucket_probe.returncode == 0 and (bucket_probe.stdout or "").strip() == "OK"
        if result.bucket_ok:
            result.message = "Ready for LABIT sync on this compute host."
        else:
            result.message = f"Bucket '{profile.rclone.bucket}' is not accessible through remote '{profile.rclone.remote}'."
        return result

    def _ssh_run(self, ssh_command: list[str], script: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*ssh_command, "bash", "-s"],
            input=script,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    def _remote_env_preamble(self) -> str:
        return 'export PATH="$HOME/.local/bin:$HOME/bin:$PATH"\n'

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
