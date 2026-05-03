from __future__ import annotations

import subprocess

from labit.models import ComputeProfile, ProjectSpec, SSHConnection
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


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

    def _load_project(self, project: str) -> ProjectSpec:
        resolved = self.project_service.resolve_project_name(project)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{project}' not found. Available projects: {', '.join(self.project_service.list_project_names()) or '(none)'}"
            )
        return self.project_service.load_project(resolved)
