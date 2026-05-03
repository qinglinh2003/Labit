from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml
from pydantic import ValidationError

from labit.models import ProjectSpec, ProjectSummary
from labit.paths import RepoPaths


PROJECT_SUBDIRS = ("digests", "sparks", "code", "docs")


class ProjectService:
    def __init__(self, paths: RepoPaths):
        self.paths = paths

    def list_project_names(self) -> list[str]:
        if not self.paths.project_configs_dir.exists():
            return []
        return sorted(path.stem for path in self.paths.project_configs_dir.glob("*.yaml"))

    def resolve_project_name(self, name: str) -> str | None:
        for candidate in self.list_project_names():
            if candidate.lower() == name.lower():
                return candidate
        return None

    def active_project_name(self) -> str | None:
        if not self.paths.active_project_path.exists():
            return None
        value = self.paths.active_project_path.read_text().strip()
        return value or None

    def load_project(self, name: str) -> ProjectSpec:
        resolved = self.resolve_project_name(name)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{name}' not found. Available projects: {', '.join(self.list_project_names()) or '(none)'}"
            )

        raw = yaml.safe_load((self.paths.project_configs_dir / f"{resolved}.yaml").read_text()) or {}
        raw = self._with_legacy_compute_profiles(raw)
        # Existing repo configs may carry legacy metadata such as `docs`.
        # Ignore unknown fields when reading repo-owned config files so
        # inspection commands remain backward-compatible.
        try:
            return ProjectSpec.model_validate(raw, extra="ignore")
        except ValidationError as exc:
            raise ValueError(
                f"Project '{resolved}' uses an outdated config format. Recreate it under the new project-profile schema.\n{exc}"
            ) from exc

    def ensure_project_dirs(self, name: str) -> Path:
        base = self.paths.vault_projects_dir / name
        for subdir in PROJECT_SUBDIRS:
            (base / subdir).mkdir(parents=True, exist_ok=True)
        return base

    def project_code_dir(self, name: str) -> Path:
        return self.paths.vault_projects_dir / name / "code"

    def save_project(self, spec: ProjectSpec, *, force: bool = False, set_active: bool = False) -> dict:
        resolved = self.resolve_project_name(spec.name)
        if resolved and not force:
            raise FileExistsError(
                f"Project '{resolved}' already exists. Re-run with '--force' to overwrite."
            )

        self.paths.project_configs_dir.mkdir(parents=True, exist_ok=True)
        config_path = self.paths.project_configs_dir / f"{spec.name}.yaml"
        yaml_text = yaml.safe_dump(spec.to_yaml_dict(), sort_keys=False, allow_unicode=True)
        self._atomic_write(config_path, yaml_text)

        project_dir = self.ensure_project_dirs(spec.name)

        if set_active:
            self.set_active_project(spec.name)

        return {
            "name": spec.name,
            "config_path": str(config_path),
            "project_dir": str(project_dir),
            "set_active": set_active,
        }

    def delete_project(self, name: str) -> dict:
        resolved = self.resolve_project_name(name)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{name}' not found. Available projects: {', '.join(self.list_project_names()) or '(none)'}"
            )

        config_path = self.paths.project_configs_dir / f"{resolved}.yaml"
        project_dir = self.paths.vault_projects_dir / resolved
        was_active = self.active_project_name() == resolved

        if config_path.exists():
            config_path.unlink()
        if project_dir.exists():
            shutil.rmtree(project_dir)
        if was_active and self.paths.active_project_path.exists():
            self.paths.active_project_path.unlink()

        return {
            "name": resolved,
            "config_path": str(config_path),
            "project_dir": str(project_dir),
            "cleared_active": was_active,
        }

    def set_active_project(self, name: str) -> None:
        resolved = self.resolve_project_name(name)
        if resolved is None:
            raise FileNotFoundError(
                f"Project '{name}' not found. Available projects: {', '.join(self.list_project_names()) or '(none)'}"
            )
        self.paths.configs_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.paths.active_project_path, f"{resolved}\n")

    def get_project_summary(self, name: str) -> ProjectSummary:
        spec = self.load_project(name)
        active = self.active_project_name()
        resolved = self.resolve_project_name(name) or spec.name
        return ProjectSummary(
            name=resolved,
            description=spec.description,
            keyword_count=len(spec.keywords),
            compute_count=len(spec.compute_profiles),
            is_active=(active == resolved),
            config_path=str(self.paths.project_configs_dir / f"{resolved}.yaml"),
        )

    def list_project_summaries(self) -> list[ProjectSummary]:
        summaries: list[ProjectSummary] = []
        for name in self.list_project_names():
            try:
                summaries.append(self.get_project_summary(name))
            except ValueError:
                continue
        return summaries

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)

    def _with_legacy_compute_profiles(self, raw: dict) -> dict:
        """Hydrate old project configs that referenced configs/compute/<name>.yaml."""
        if raw.get("compute_profiles"):
            return raw

        legacy_name = str(raw.get("compute_profile") or "").strip()
        if not legacy_name:
            return raw

        legacy_path = self.paths.configs_dir / "compute" / f"{legacy_name}.yaml"
        if not legacy_path.exists():
            return raw

        legacy = yaml.safe_load(legacy_path.read_text()) or {}
        connection = legacy.get("connection") or {}
        workspace = legacy.get("workspace") or {}
        notes = []
        datadir = str(workspace.get("datadir") or "").strip()
        if datadir:
            notes.append(f"legacy datadir: {datadir}")

        profile = {
            "name": str(legacy.get("name") or legacy_name).strip(),
            "connection": {
                "user": str(connection.get("user") or "").strip(),
                "host": str(connection.get("host") or "").strip(),
                "port": int(connection.get("port") or 22),
                "identity_file": connection.get("identity_file") or connection.get("ssh_key"),
            },
            "workdir": str(workspace.get("workdir") or "").strip(),
            "notes": "; ".join(notes),
        }

        updated = dict(raw)
        updated["compute_profiles"] = [profile]
        return updated
