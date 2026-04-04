from __future__ import annotations

from pathlib import Path

from labit.agents.models import CodeSnapshot, ContextPack, MemorySnapshot, ProjectSnapshot, TaskSpec, WorkspaceSnapshot
from labit.hypotheses.service import HypothesisService
from labit.papers.service import PaperService
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


class ContextBuilder:
    def __init__(self, paths: RepoPaths):
        self.paths = paths
        self.project_service = ProjectService(paths)
        self.paper_service = PaperService(paths)
        self.hypothesis_service = HypothesisService(paths, project_service=self.project_service)

    def build(self, task: TaskSpec, *, project_name: str | None = None) -> ContextPack:
        resolved_project = project_name or self.project_service.active_project_name()
        project_snapshot = None
        memory = MemorySnapshot()
        code_snapshot = None

        if resolved_project:
            spec = self.project_service.load_project(resolved_project)
            project_snapshot = ProjectSnapshot(
                name=resolved_project,
                description=spec.description,
                keywords=spec.keywords,
                relevance_criteria=spec.relevance_criteria,
            )

            memory.open_hypotheses = [
                {
                    "id": item.hypothesis_id,
                    "title": item.title,
                    "state": item.state.value,
                    "resolution": item.resolution.value,
                    "status": item.status.value,
                    "path": item.path,
                }
                for item in self.hypothesis_service.list_hypotheses(resolved_project)
                if item.state.value == "open"
            ][:10]

            memory.key_papers = [
                entry.model_dump(mode="json")
                for entry in self.paper_service.list_project_index_entries(resolved_project)[:10]
            ]
            code_snapshot = self._build_code_snapshot(resolved_project)

        memory.global_matches = [
            entry.model_dump(mode="json")
            for entry in self.paper_service.list_global_index_entries()[:10]
        ]

        workspace = WorkspaceSnapshot(
            repo_root=str(self.paths.root),
            allowed_write_scope=[str(Path(path)) for path in task.write_scope],
        )

        return ContextPack(
            project=project_snapshot,
            task=task,
            memory=memory,
            code=code_snapshot,
            workspace=workspace,
        )

    def _build_code_snapshot(self, project: str) -> CodeSnapshot | None:
        code_dir = self.paths.vault_projects_dir / project / "code"
        if not code_dir.exists():
            return None

        readme_excerpt = ""
        for candidate in ("README.md", "readme.md"):
            path = code_dir / candidate
            if path.exists():
                readme_excerpt = self._read_excerpt(path)
                break

        package_roots = sorted(
            str(path.relative_to(self.paths.root))
            for path in code_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".") and (path / "__init__.py").exists()
        )[:5]
        entrypoints = sorted(
            str(path.relative_to(self.paths.root))
            for path in (code_dir / "scripts").rglob("*.py")
        )[:8] if (code_dir / "scripts").exists() else []
        config_files = sorted(
            str(path.relative_to(self.paths.root))
            for path in (code_dir / "configs").rglob("*.yaml")
        )[:8] if (code_dir / "configs").exists() else []

        notes: list[str] = []
        if (code_dir / "pyproject.toml").exists():
            notes.append(str((code_dir / "pyproject.toml").relative_to(self.paths.root)))
        if (code_dir / "README.md").exists():
            notes.append(str((code_dir / "README.md").relative_to(self.paths.root)))

        return CodeSnapshot(
            project_code_dir=str(code_dir.relative_to(self.paths.root)),
            readme_excerpt=readme_excerpt,
            package_roots=package_roots,
            entrypoints=entrypoints,
            config_files=config_files,
            notes=notes[:8],
        )

    def _read_excerpt(self, path: Path, *, max_chars: int = 2400) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        return text[:max_chars].strip()
