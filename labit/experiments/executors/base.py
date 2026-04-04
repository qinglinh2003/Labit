from __future__ import annotations

from abc import ABC, abstractmethod

from labit.experiments.models import LaunchArtifact, SubmissionReceipt


class ExperimentExecutor(ABC):
    @abstractmethod
    def prepare(self, artifact: LaunchArtifact) -> LaunchArtifact:
        raise NotImplementedError

    @abstractmethod
    def submit(self, artifact: LaunchArtifact) -> SubmissionReceipt:
        raise NotImplementedError

    @abstractmethod
    def poll(self, artifact: LaunchArtifact) -> dict:
        raise NotImplementedError

    @abstractmethod
    def collect(self, artifact: LaunchArtifact) -> dict:
        raise NotImplementedError

    @abstractmethod
    def cancel(self, artifact: LaunchArtifact) -> dict:
        raise NotImplementedError
