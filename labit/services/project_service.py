from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml
from pydantic import ValidationError

from labit.models import ProjectDraft, ProjectSeed, ProjectSpec, ProjectSummary, SemanticBrief
from labit.paths import RepoPaths


PROJECT_SUBDIRS = ("digests", "sparks", "hypotheses", "tasks", "code", "docs")


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
        # Existing repo configs may carry legacy metadata such as `docs`.
        # Ignore unknown fields when reading repo-owned config files so
        # inspection commands remain backward-compatible.
        try:
            return ProjectSpec.model_validate(raw, extra="ignore")
        except ValidationError as exc:
            raise ValueError(
                f"Project '{resolved}' uses an outdated config format. Recreate it under the new compute-profile schema.\n{exc}"
            ) from exc

    def load_project_seed(self, seed_path: Path) -> ProjectSeed:
        raw = yaml.safe_load(seed_path.read_text()) or {}
        return ProjectSeed.model_validate(raw)

    def load_semantic_brief(self, brief_path: Path) -> SemanticBrief:
        raw = yaml.safe_load(brief_path.read_text()) or {}
        return SemanticBrief.model_validate(raw)

    def load_project_spec(self, spec_path: Path) -> ProjectSpec:
        raw = yaml.safe_load(spec_path.read_text()) or {}
        return ProjectSpec.model_validate(raw)

    def build_project_draft(self, brief: SemanticBrief) -> ProjectDraft:
        return ProjectDraft.scaffold_from_brief(brief)

    def compose_project_spec(self, seed: ProjectSeed, draft: ProjectDraft) -> ProjectSpec:
        return ProjectSpec.from_seed_and_draft(seed, draft)

    def project_exists(self, name: str) -> bool:
        return self.resolve_project_name(name) is not None

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
            paper_count=self.count_project_papers(resolved),
            hypothesis_count=self.count_project_hypotheses(resolved),
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

    def planned_create_actions(self, spec: ProjectSpec, *, set_active: bool = False) -> list[str]:
        actions = [
            f"write config: {self.paths.project_configs_dir / f'{spec.name}.yaml'}",
            f"create project overlay: {self.paths.vault_projects_dir / spec.name}",
        ]
        for subdir in PROJECT_SUBDIRS:
            actions.append(f"ensure dir: {self.paths.vault_projects_dir / spec.name / subdir}")
        if set_active:
            actions.append(f"set active project: {spec.name}")
        return actions

    def planned_clone_action(self, spec: ProjectSpec) -> str | None:
        if not spec.repo:
            return None
        return f"git clone {spec.repo} {self.project_code_dir(spec.name)}"

    def clone_project_code(self, name: str) -> dict:
        spec = self.load_project(name)
        if not spec.repo:
            raise ValueError(f"Project '{spec.name}' does not declare a repository URL.")

        target_dir = self.project_code_dir(spec.name)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_dir.exists() and any(target_dir.iterdir()):
            raise FileExistsError(
                f"Code directory already exists and is not empty: {target_dir}"
            )

        cmd = ["git", "clone", spec.repo, str(target_dir)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RuntimeError(f"git clone failed: {detail}") from exc

        return {
            "name": spec.name,
            "repo": spec.repo,
            "target_dir": str(target_dir),
        }

    def count_project_hypotheses(self, name: str) -> int:
        path = self.paths.vault_projects_dir / name / "hypotheses"
        if not path.exists():
            return 0
        structured_ids = {
            item.name
            for item in path.iterdir()
            if item.is_dir() and (item / "hypothesis.yaml").exists() and re.fullmatch(r"h\d+", item.name)
        }
        legacy_ids = {
            item.stem
            for item in path.glob("h*.yaml")
            if re.fullmatch(r"h\d+", item.stem)
        }
        return len(structured_ids | legacy_ids)

    def count_project_papers(self, name: str) -> int:
        index_path = self.paths.vault_projects_dir / name / "papers.yaml"
        if index_path.exists():
            data = yaml.safe_load(index_path.read_text()) or []
            if isinstance(data, list):
                return len(data)

        if not self.paths.papers_dir.exists():
            return 0

        count = 0
        pattern = re.compile(r"relevance_to:\s*\[([^\]]*)\]")
        for path in self.paths.papers_dir.glob("*.md"):
            content = path.read_text()
            match = pattern.search(content)
            if not match:
                continue
            values = [item.strip() for item in match.group(1).split(",") if item.strip()]
            if name in values:
                count += 1
        return count

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
