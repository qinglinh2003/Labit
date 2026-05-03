from __future__ import annotations

from dataclasses import dataclass

from labit.agents.adapters import ClaudeAdapter, CodexAdapter
from labit.agents.adapters.base import AgentAdapter
from labit.agents.models import ProviderKind


@dataclass
class ProviderRegistry:
    adapters: dict[ProviderKind, AgentAdapter]

    @classmethod
    def default(cls) -> "ProviderRegistry":
        return cls(
            adapters={
                ProviderKind.CLAUDE: ClaudeAdapter(),
                ProviderKind.CODEX: CodexAdapter(),
            }
        )

    def get(self, provider: ProviderKind) -> AgentAdapter:
        try:
            return self.adapters[provider]
        except KeyError as exc:
            raise KeyError(f"No adapter registered for provider '{provider.value}'.") from exc
