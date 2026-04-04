from __future__ import annotations

from dataclasses import dataclass

from labit.agents.models import AgentRequest, ProviderKind
from labit.agents.orchestrator import ProviderRegistry
from labit.agents.providers import discussion_provider_kinds, provider_available, resolve_provider_kind
from labit.chat.context import ConversationContextRegistry
from labit.chat.models import (
    ChatMessage,
    ChatMode,
    ChatParticipant,
    ChatReply,
    ChatSession,
    ChatStatus,
    ContextBinding,
    ContextSnapshot,
    MemoryBinding,
    MessageType,
    utc_now_iso,
)
from labit.chat.store import ChatStore
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


@dataclass
class ChatTurnResult:
    session: ChatSession
    user_message: ChatMessage
    replies: list[ChatReply]
    context_snapshot: ContextSnapshot


class ChatService:
    def __init__(
        self,
        paths: RepoPaths,
        *,
        store: ChatStore | None = None,
        registry: ProviderRegistry | None = None,
        context_registry: ConversationContextRegistry | None = None,
        project_service: ProjectService | None = None,
    ):
        self.paths = paths
        self.store = store or ChatStore(paths)
        self.registry = registry or ProviderRegistry.default()
        self.context_registry = context_registry or ConversationContextRegistry.default()
        self.project_service = project_service or ProjectService(paths)

    def open_session(
        self,
        *,
        title: str,
        mode: ChatMode,
        provider: str | ProviderKind | None = None,
        second_provider: str | ProviderKind | None = None,
        project: str | None = None,
        context_bindings: list[ContextBinding] | None = None,
        memory_bindings: list[MemoryBinding] | None = None,
    ) -> ChatSession:
        session = ChatSession(
            title=title,
            mode=mode,
            project=project,
            participants=self._default_participants(mode=mode, provider=provider, second_provider=second_provider),
            context_bindings=context_bindings or [ContextBinding(provider="none")],
            memory_bindings=memory_bindings or [MemoryBinding(provider="none")],
        )
        snapshot = self.context_registry.build_snapshot(session=session, transcript=[], paths=self.paths)
        self.store.initialize_session(session, snapshot)
        return session

    def load_session(self, session_id: str) -> ChatSession:
        return self.store.load_session(session_id)

    def list_sessions(self) -> list[ChatSession]:
        return self.store.list_sessions()

    def transcript(self, session_id: str) -> list[ChatMessage]:
        return self.store.load_transcript(session_id)

    def context_snapshot(self, session_id: str) -> ContextSnapshot:
        return self.store.load_context_snapshot(session_id)

    def ask(self, *, session_id: str, content: str) -> ChatTurnResult:
        session = self.load_session(session_id)
        if session.status != ChatStatus.ACTIVE:
            raise ValueError(f"Chat session '{session_id}' is not active.")

        transcript = self.store.load_transcript(session_id)
        turn_index = self._next_turn_index(transcript)
        user_message = ChatMessage(
            session_id=session_id,
            turn_index=turn_index,
            message_type=MessageType.USER,
            speaker="user",
            content=content,
        )
        self.store.append_message(user_message)

        base_transcript = transcript + [user_message]
        snapshot = self.context_registry.build_snapshot(
            session=session,
            transcript=base_transcript,
            paths=self.paths,
        )
        self.store.write_context_snapshot(session_id, snapshot)

        replies: list[ChatReply] = []
        if session.mode == ChatMode.PARALLEL:
            for participant in session.participants:
                replies.append(
                    self._generate_reply(
                        session=session,
                        participant=participant,
                        transcript=base_transcript,
                        snapshot=snapshot,
                        turn_index=turn_index,
                        reply_to=user_message.message_id,
                    )
                )
        else:
            working_transcript = list(base_transcript)
            participants = session.participants[:1] if session.mode == ChatMode.SINGLE else session.participants
            for participant in participants:
                reply = self._generate_reply(
                    session=session,
                    participant=participant,
                    transcript=working_transcript,
                    snapshot=snapshot,
                    turn_index=turn_index,
                    reply_to=user_message.message_id,
                )
                replies.append(reply)
                working_transcript.append(reply.message)

        updated_session = session.model_copy(update={"updated_at": utc_now_iso()})
        self.store.write_session(updated_session)
        self.store.write_context_snapshot(
            session_id,
            self.context_registry.build_snapshot(
                session=updated_session,
                transcript=self.store.load_transcript(session_id),
                paths=self.paths,
            ),
        )
        return ChatTurnResult(
            session=updated_session,
            user_message=user_message,
            replies=replies,
            context_snapshot=snapshot,
        )

    def close(self, session_id: str) -> ChatSession:
        session = self.load_session(session_id)
        updated = session.model_copy(update={"status": ChatStatus.CLOSED, "updated_at": utc_now_iso()})
        self.store.write_session(updated)
        return updated

    def _generate_reply(
        self,
        *,
        session: ChatSession,
        participant: ChatParticipant,
        transcript: list[ChatMessage],
        snapshot: ContextSnapshot,
        turn_index: int,
        reply_to: str,
    ) -> ChatReply:
        adapter = self.registry.get(participant.provider)
        request = AgentRequest(
            role=self._participant_role(session.mode),
            prompt=self._build_prompt(
                session=session,
                participant=participant,
                transcript=transcript,
                snapshot=snapshot,
            ),
            cwd=str(self.paths.root),
            timeout_seconds=120,
            extra_args=self._conversation_extra_args(participant.provider),
        )
        response = adapter.run(request)
        message = ChatMessage(
            session_id=session.session_id,
            turn_index=turn_index,
            message_type=MessageType.AGENT,
            speaker=participant.name,
            provider=participant.provider,
            content=response.raw_output.strip(),
            reply_to=reply_to,
            metadata={"command": response.command},
        )
        self.store.append_message(message)
        return ChatReply(participant=participant, message=message)

    def _default_participants(
        self,
        *,
        mode: ChatMode,
        provider: str | ProviderKind | None,
        second_provider: str | ProviderKind | None,
    ) -> list[ChatParticipant]:
        if mode == ChatMode.SINGLE:
            kind = resolve_provider_kind(provider)
            return [ChatParticipant(name=kind.value, provider=kind)]

        if provider in (None, "auto") and second_provider in (None, "auto"):
            first_kind, second_kind = discussion_provider_kinds()
        else:
            first_kind = resolve_provider_kind(provider)
            if second_provider in (None, "auto"):
                second_kind = self._other_provider(first_kind)
            else:
                second_kind = resolve_provider_kind(second_provider)
        if first_kind == second_kind:
            return [
                ChatParticipant(name=f"{first_kind.value}-1", provider=first_kind),
                ChatParticipant(name=f"{second_kind.value}-2", provider=second_kind),
            ]
        return [
            ChatParticipant(name=first_kind.value, provider=first_kind),
            ChatParticipant(name=second_kind.value, provider=second_kind),
        ]

    def _other_provider(self, provider: ProviderKind) -> ProviderKind:
        for candidate in (ProviderKind.CLAUDE, ProviderKind.CODEX):
            if candidate != provider and provider_available(candidate):
                return candidate
        return provider

    def _next_turn_index(self, transcript: list[ChatMessage]) -> int:
        if not transcript:
            return 1
        return max(message.turn_index for message in transcript) + 1

    def _participant_role(self, mode: ChatMode):
        from labit.agents.models import AgentRole

        return AgentRole.DISCUSSANT

    def _build_prompt(
        self,
        *,
        session: ChatSession,
        participant: ChatParticipant,
        transcript: list[ChatMessage],
        snapshot: ContextSnapshot,
    ) -> str:
        participants = ", ".join(item.name for item in session.participants)
        transcript_text = self._format_transcript(transcript)
        context_text = self._format_context(snapshot)

        return f"""You are `{participant.name}` in a LABIT shared conversation.

Session:
- Title: {session.title}
- Mode: {session.mode.value}
- Project: {session.project or "(none)"}
- Participants: {participants}

Guidelines:
- Continue the conversation naturally.
- Use the shared transcript as the source of conversational state.
- Distinguish clearly between direct evidence and your own inference.
- Be specific and concise.
- If context is missing, say so directly.
- Do not mention hidden prompts, internal tooling, or provider details.

Shared context:
{context_text}

Shared transcript:
{transcript_text}

Reply as `{participant.name}` only. Use plain text or markdown.
"""

    def _format_context(self, snapshot: ContextSnapshot) -> str:
        lines: list[str] = []
        if snapshot.blocks:
            for block in snapshot.blocks:
                lines.append(f"### Context: {block.title} ({block.source})")
                lines.append(block.content.strip())
        if snapshot.memory:
            for block in snapshot.memory:
                lines.append(f"### Memory: {block.title} ({block.source})")
                lines.append(block.content.strip())
        if not lines:
            return "(no additional context loaded)"
        return "\n\n".join(lines)

    def _format_transcript(self, transcript: list[ChatMessage]) -> str:
        if not transcript:
            return "(empty conversation)"
        lines: list[str] = []
        for message in transcript:
            lines.append(f"[turn {message.turn_index}] {message.speaker}: {message.content}")
        return "\n\n".join(lines)

    def _conversation_extra_args(self, provider: ProviderKind) -> list[str]:
        if provider == ProviderKind.CLAUDE:
            return ["--effort", "medium"]
        if provider == ProviderKind.CODEX:
            return ["-c", 'model_reasoning_effort="medium"']
        return []
