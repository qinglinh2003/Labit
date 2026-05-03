from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from labit.chat.models import (
    ChatMessage,
    ChatSession,
    ContextBinding,
    ContextBlock,
    ContextSnapshot,
    MemoryBinding,
    MemoryBlock,
)
from labit.context.store import SessionContextStore
from labit.paths import RepoPaths


class ConversationContextProvider(ABC):
    name: str

    @abstractmethod
    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: ContextBinding,
        paths: RepoPaths,
    ) -> list[ContextBlock]:
        raise NotImplementedError


class ConversationMemoryProvider(ABC):
    name: str

    @abstractmethod
    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: MemoryBinding,
        paths: RepoPaths,
    ) -> list[MemoryBlock]:
        raise NotImplementedError


class EmptyContextProvider(ConversationContextProvider):
    name = "none"

    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: ContextBinding,
        paths: RepoPaths,
    ) -> list[ContextBlock]:
        return []


class EmptyMemoryProvider(ConversationMemoryProvider):
    name = "none"

    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: MemoryBinding,
        paths: RepoPaths,
    ) -> list[MemoryBlock]:
        return []


class SessionWorkingMemoryProvider(ConversationMemoryProvider):
    name = "session_working_memory"

    def build(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        binding: MemoryBinding,
        paths: RepoPaths,
    ) -> list[MemoryBlock]:
        store = SessionContextStore(paths)
        snapshot = store.load_working_memory(session.session_id)
        if snapshot is None:
            return []

        lines: list[str] = []
        if snapshot.current_goal:
            lines.append(f"Current goal: {snapshot.current_goal}")
        if snapshot.active_artifacts:
            lines.append(f"Active artifacts: {', '.join(snapshot.active_artifacts)}")
        if snapshot.decisions_made:
            lines.append("Decisions:")
            lines.extend(f"- {item}" for item in snapshot.decisions_made)
        if snapshot.open_questions:
            lines.append("Open questions:")
            lines.extend(f"- {item}" for item in snapshot.open_questions)
        if snapshot.discussion_state.consensus:
            lines.append("Consensus:")
            lines.extend(f"- {item}" for item in snapshot.discussion_state.consensus)
        if snapshot.discussion_state.disagreements:
            lines.append("Disagreements:")
            lines.extend(f"- {item}" for item in snapshot.discussion_state.disagreements)
        if snapshot.followups:
            lines.append("Follow-ups:")
            lines.extend(f"- {item}" for item in snapshot.followups)
        if snapshot.evidence_refs:
            lines.append("Evidence refs:")
            lines.extend(f"- {item}" for item in snapshot.evidence_refs)
        if not lines:
            return []

        return [
            MemoryBlock(
                source=self.name,
                title="Session Working Memory",
                content="\n".join(lines),
            )
        ]


@dataclass
class ConversationContextRegistry:
    context_providers: dict[str, ConversationContextProvider]
    memory_providers: dict[str, ConversationMemoryProvider]

    @classmethod
    def default(cls) -> "ConversationContextRegistry":
        return cls(
            context_providers={
                EmptyContextProvider.name: EmptyContextProvider(),
            },
            memory_providers={
                EmptyMemoryProvider.name: EmptyMemoryProvider(),
                SessionWorkingMemoryProvider.name: SessionWorkingMemoryProvider(),
            },
        )

    def build_snapshot(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        paths: RepoPaths,
    ) -> ContextSnapshot:
        context_blocks: list[ContextBlock] = []
        memory_blocks: list[MemoryBlock] = []

        for binding in session.context_bindings:
            provider = self.context_providers.get(binding.provider)
            if provider is None:
                raise KeyError(f"Unknown context provider '{binding.provider}'.")
            context_blocks.extend(
                provider.build(session=session, transcript=transcript, binding=binding, paths=paths)
            )

        for binding in session.memory_bindings:
            provider = self.memory_providers.get(binding.provider)
            if provider is None:
                raise KeyError(f"Unknown memory provider '{binding.provider}'.")
            memory_blocks.extend(
                provider.build(session=session, transcript=transcript, binding=binding, paths=paths)
            )

        return ContextSnapshot(blocks=context_blocks, memory=memory_blocks)
