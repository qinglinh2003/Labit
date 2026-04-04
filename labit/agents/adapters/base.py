from __future__ import annotations

from abc import ABC, abstractmethod

from labit.agents.models import AgentRequest, AgentResponse, ProviderKind


class AgentAdapterError(RuntimeError):
    """Raised when an agent backend fails."""


class AgentAdapter(ABC):
    provider: ProviderKind

    @abstractmethod
    def run(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError
