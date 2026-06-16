from __future__ import annotations

import subprocess
import shlex
from dataclasses import dataclass
from pathlib import Path

from labit.models import ComputeProfile, ProjectSpec, SSHConnection
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


# Default patterns excluded from rsync code sync
DEFAULT_SYNC_EXCLUDES = [
    ".git/",
    "__pycache__/",
    "*.pyc",
    ".venv/",
    "venv/",
    "node_modules/",
    ".eggs/",
    "*.egg-info/",
    "dist/",
    "build/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".pytest_cache/",
    "wandb/",
    "outputs/",
    "runs/",
    ".history/",
]


@dataclass
class SyncResult:
    success: bool
    profile_name: str
    local_path: str
    remote_path: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class ComputeService:
    def __init__(self, paths: RepoPaths, *, project_service: ProjectService | None = None):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)

    def list_profiles(self, project: str) -> list[ComputeProfile]:
        return self._load_project(project).compute_profiles

    def get_profile(self, project: str, name: str) -> ComputeProfile:
        for profile in self.list_profiles(project):
            if profile.name.lower() == name.lower():
                return profile
        raise FileNotFoundError(f"Compute profile '{name}' not found in project '{project}'.")

    def save_profile(self, project: str, profile: ComputeProfile) -> ProjectSpec:
        spec = self._load_project(project)
        profiles = [item for item in spec.compute_profiles if item.name.lower() != profile.name.lower()]
        profiles.append(profile)
        profiles.sort(key=lambda item: item.name.lower())
        updated = spec.model_copy(update={"compute_profiles": profiles})
        self.project_service.save_project(updated, force=True, set_active=False)
        return updated

    def delete_profile(self, project: str, name: str) -> ProjectSpec:
        spec = self._load_project(project)
        profiles = [item for item in spec.compute_profiles if item.name.lower() != name.lower()]
        if len(profiles) == len(spec.compute_profiles):
            raise FileNotFoundError(f"Compute profile '{name}' not found in project '{project}'.")
        updated = spec.model_copy(update={"compute_profiles": profiles})
        self.project_service.save_project(updated, force=True, set_active=False)
        return updated

    def build_profile(
        self,
        *,
        name: str,
        user: str,
        host: str,
        port: int = 22,
        identity_file: str | None = None,
        workdir: str = "",
        cache_dir: str = ".cache",
        notes: str = "",
    ) -> ComputeProfile:
        return ComputeProfile(
            name=name,
            connection=SSHConnection(
                user=user,
                host=host,
                port=port,
                identity_file=identity_file,
            ),
            workdir=workdir,
            cache_dir=cache_dir,
            notes=notes,
        )

    def test_profile(self, project: str, name: str, *, timeout_seconds: int = 8) -> subprocess.CompletedProcess[str]:
        profile = self.get_profile(project, name)
        ssh_command = profile.ssh_command()
        command = [
            *ssh_command[:-1],
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={timeout_seconds}",
            ssh_command[-1],
            "printf 'labit-ssh-ok\\n'",
        ]
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout_seconds + 2,
            check=False,
        )

    def sync_code(
        self,
        project: str,
        profile_name: str,
        *,
        excludes: list[str] | None = None,
    ) -> SyncResult:
        """Rsync project code/ directory to the remote profile's workdir."""
        profile = self.get_profile(project, profile_name)
        local_code = Path(self.project_service.project_dir(project)) / "code"
        if not profile.workdir:
            return SyncResult(
                success=False,
                profile_name=profile_name,
                local_path=str(local_code),
                remote_path="",
                stderr=f"Compute profile '{profile.name}' must define a remote workdir before code can be synced.",
                returncode=-1,
            )
        if not local_code.exists():
            return SyncResult(
                success=False,
                profile_name=profile_name,
                local_path=str(local_code),
                remote_path=profile.workdir,
                stderr=f"Local code directory does not exist: {local_code}",
                returncode=-1,
            )

        # Normalise fullwidth tilde (U+FF5E) → ASCII tilde so rsync resolves ~
        workdir = profile.workdir.replace("\uff5e", "~")
        remote_target = f"{profile.connection.target}:{workdir}/"

        # Build rsync command
        cmd = ["rsync", "-azP", "--delete"]
        all_excludes = list(excludes or DEFAULT_SYNC_EXCLUDES)
        # Always exclude the profile's cache directory from code sync
        if profile.cache_dir:
            cache_pattern = profile.cache_dir.strip("/") + "/"
            if cache_pattern not in all_excludes:
                all_excludes.append(cache_pattern)
        for pattern in all_excludes:
            cmd.extend(["--exclude", pattern])

        # SSH options
        ssh_parts = ["ssh"]
        if profile.connection.identity_file:
            ssh_parts.extend(["-i", str(Path(profile.connection.identity_file).expanduser())])
        if profile.connection.port != 22:
            ssh_parts.extend(["-p", str(profile.connection.port)])
        cmd.extend(["-e", shlex.join(ssh_parts)])

        # source (trailing slash = contents, not the dir itself)
        cmd.append(str(local_code) + "/")
        cmd.append(remote_target)

        result = subprocess.run(
            cmd, text=True, capture_output=True,
            timeout=None, check=False,
        )

        return SyncResult(
            success=result.returncode == 0,
            profile_name=profile_name,
            local_path=str(local_code),
            remote_path=f"{profile.connection.target}:{profile.workdir}",
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )

    def check_gpu(
        self, project: str, profile_name: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run nvidia-smi on the remote machine and return the output."""
        profile = self.get_profile(project, profile_name)
        ssh_cmd = profile.ssh_command()
        command = [
            *ssh_cmd[:-1],
            "-o", "BatchMode=yes",
            ssh_cmd[-1],
            "nvidia-smi",
        ]
        return subprocess.run(
            command, text=True, capture_output=True,
            timeout=None, check=False,
        )

    def _load_project(self, project: str) -> ProjectSpec:
        resolved = self.project_service.resolve_project_name(project)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{project}' not found. Available projects: {', '.join(self.project_service.list_project_names()) or '(none)'}"
            )
        return self.project_service.load_project(resolved)
