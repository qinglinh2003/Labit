from __future__ import annotations

from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


class SyncService:
    def __init__(self, paths: RepoPaths, *, project_service: ProjectService | None = None):
        self.paths = paths
        self.project_service = project_service or ProjectService(paths)

    def status(self, project: str):
        raise NotImplementedError("Sync is being redesigned around compute/storage profiles and is temporarily unavailable.")

    def push(self, project: str):
        raise NotImplementedError("Sync is being redesigned around compute/storage profiles and is temporarily unavailable.")

    def pull(self, project: str):
        raise NotImplementedError("Sync is being redesigned around compute/storage profiles and is temporarily unavailable.")
