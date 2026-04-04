from __future__ import annotations

from labit.agents.models import ContextPack, MemorySnapshot, ProjectSnapshot, TaskSpec, WorkspaceSnapshot
from labit.codebase.map import CodeMapBuilder
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
        self.code_map_builder = CodeMapBuilder(paths)

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
            code_snapshot = self.code_map_builder.build_snapshot(resolved_project)

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
