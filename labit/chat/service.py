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
from labit.context.assembler import ContextAssembler, ContextSection
from labit.context.budget import TokenBudget
from labit.context.condenser import ResearchRollingCondenser, SessionCondenser
from labit.context.events import SessionEvent, SessionEventKind, WorkingMemorySnapshot
from labit.context.maps import ContextMapBuilder
from labit.context.store import SessionContextStore
from labit.memory.retrievers import MemoryRetriever
from labit.memory.service import MemoryService
from labit.memory.store import MemoryStore
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
        session_context_store: SessionContextStore | None = None,
        condenser: SessionCondenser | None = None,
        assembler: ContextAssembler | None = None,
        context_map_builder: ContextMapBuilder | None = None,
        memory_store: MemoryStore | None = None,
        memory_service: MemoryService | None = None,
        memory_retriever: MemoryRetriever | None = None,
    ):
        self.paths = paths
        self.store = store or ChatStore(paths)
        self.registry = registry or ProviderRegistry.default()
        self.context_registry = context_registry or ConversationContextRegistry.default()
        self.project_service = project_service or ProjectService(paths)
        self.session_context_store = session_context_store or SessionContextStore(paths)
        self.condenser = condenser or ResearchRollingCondenser()
        self.assembler = assembler or ContextAssembler(
            budget=TokenBudget(total_tokens=120000, reserve_tokens=20000)
        )
        self.context_map_builder = context_map_builder or ContextMapBuilder(paths)
        self.memory_store = memory_store or MemoryStore(paths)
        self.memory_service = memory_service or MemoryService(paths, store=self.memory_store)
        self.memory_retriever = memory_retriever or MemoryRetriever(self.memory_store)

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
            memory_bindings=memory_bindings or [MemoryBinding(provider="session_working_memory")],
        )
        snapshot = self.context_registry.build_snapshot(session=session, transcript=[], paths=self.paths)
        self.store.initialize_session(session, snapshot)
        self.session_context_store.write_working_memory(
            WorkingMemorySnapshot(session_id=session.session_id, project=session.project)
        )
        for binding in session.context_bindings:
            if binding.provider == "paper_focus":
                paper_id = str(binding.config.get("paper_id", "")).strip()
                if paper_id:
                    self.session_context_store.append_event(
                        SessionEvent(
                            session_id=session.session_id,
                            project=session.project,
                            kind=SessionEventKind.ARTIFACT_FOCUS_BOUND,
                            actor="system",
                            summary=f"Bound paper focus context for {paper_id}",
                            payload={"provider": binding.provider, "config": binding.config},
                            evidence_refs=[f"paper:{paper_id}"],
                        )
                    )
        self._refresh_working_memory(session)
        self.store.write_context_snapshot(
            session.session_id,
            self.context_registry.build_snapshot(session=session, transcript=[], paths=self.paths),
        )
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
        self._append_message_event(session=session, message=user_message)
        self._refresh_working_memory(session)

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
        self._refresh_working_memory(updated_session)
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

    def record_session_event(
        self,
        *,
        session_id: str,
        kind: SessionEventKind,
        summary: str,
        actor: str = "system",
        payload: dict | None = None,
        evidence_refs: list[str] | None = None,
        turn_index: int | None = None,
    ) -> SessionEvent:
        session = self.load_session(session_id)
        event = SessionEvent(
            session_id=session.session_id,
            project=session.project,
            kind=kind,
            turn_index=turn_index,
            actor=actor,
            summary=summary,
            payload=payload or {},
            evidence_refs=evidence_refs or [],
        )
        self.session_context_store.append_event(event)
        self._promote_event_to_memory(event)
        self._refresh_working_memory(session)
        self.store.write_context_snapshot(
            session.session_id,
            self.context_registry.build_snapshot(
                session=session,
                transcript=self.store.load_transcript(session.session_id),
                paths=self.paths,
            ),
        )
        return event

    def record_discussion_synthesis(
        self,
        *,
        session_id: str,
        summary: str,
        consensus: list[str] | None = None,
        disagreements: list[str] | None = None,
        followups: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        actor: str = "labit",
    ) -> SessionEvent:
        payload = {
            "consensus": consensus or [],
            "disagreements": disagreements or [],
            "followups": followups or [],
        }
        return self.record_session_event(
            session_id=session_id,
            kind=SessionEventKind.DISCUSSION_SYNTHESIS,
            actor=actor,
            summary=summary,
            payload=payload,
            evidence_refs=evidence_refs or [],
        )

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
        self._append_message_event(session=session, message=message)
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
        assembled_context = self._assemble_context(session=session, transcript=transcript, snapshot=snapshot)

        return f"""You are `{participant.name}` in a LABIT shared conversation.

Session:
- Title: {session.title}
- Mode: {session.mode.value}
- Project: {session.project or "(none)"}
- Participants: {participants}

Guidelines:
- Continue the conversation naturally.
- Use the assembled context as the source of conversational state.
- Distinguish clearly between direct evidence and your own inference.
- Be specific and concise.
- If context is missing, say so directly.
- Do not mention hidden prompts, internal tooling, or provider details.

Assembled context:
{assembled_context}

Reply as `{participant.name}` only. Use plain text or markdown.
"""

    def _assemble_context(
        self,
        *,
        session: ChatSession,
        transcript: list[ChatMessage],
        snapshot: ContextSnapshot,
    ) -> str:
        task_header = "\n".join(
            [
                f"Session title: {session.title}",
                f"Project: {session.project or '(none)'}",
                f"Mode: {session.mode.value}",
                f"Participants: {', '.join(item.name for item in session.participants)}",
            ]
        )
        working_memory = self.session_context_store.load_working_memory(session.session_id)
        evidence_refs = list((working_memory.evidence_refs if working_memory else []))
        base_query_text = self._memory_query_text(transcript=transcript, working_memory=working_memory)
        bound_sections = [
            ContextSection(title=block.title, content=block.content, source=block.source, priority=90)
            for block in snapshot.blocks
        ]
        recent_sections = []
        recent_transcript = self._format_recent_transcript(transcript)
        if recent_transcript:
            recent_sections.append(
                ContextSection(
                    title="Recent Transcript",
                    content=recent_transcript,
                    source="transcript",
                    priority=80,
                )
            )
        map_sections = self.context_map_builder.build_sections(
            project=session.project,
            query=base_query_text,
            evidence_refs=evidence_refs,
            exclude_paper_ids=self._bound_paper_ids(session),
        )
        memory_query_text = self.context_map_builder.shape_memory_query(
            base_query=base_query_text,
            sections=map_sections,
        )
        retrieved_memories = []
        if session.project:
            retrieved_memories = self.memory_retriever.retrieve(
                project=session.project,
                query=memory_query_text,
                evidence_refs=evidence_refs,
                limit=6,
            )
        assembled = self.assembler.assemble(
            task_header=task_header,
            bound_sections=bound_sections,
            recent_sections=recent_sections,
            working_memory=working_memory,
            memories=retrieved_memories,
            map_sections=map_sections,
        )
        return assembled.render()

    def _format_recent_transcript(self, transcript: list[ChatMessage]) -> str:
        if not transcript:
            return "(empty conversation)"
        recent = self._recent_turn_window(transcript, max_turns=50)
        lines: list[str] = []
        for message in recent:
            provider = f" ({message.provider.value})" if message.provider else ""
            lines.append(f"[turn {message.turn_index}] {message.speaker}{provider}: {message.content}")
        rendered = "\n\n".join(lines)
        return self.assembler.clip_to_tokens(rendered, max_tokens=60000)

    def _recent_turn_window(self, transcript: list[ChatMessage], *, max_turns: int) -> list[ChatMessage]:
        if not transcript:
            return []
        ordered_turns: list[int] = []
        seen: set[int] = set()
        for message in reversed(transcript):
            if message.turn_index in seen:
                continue
            ordered_turns.append(message.turn_index)
            seen.add(message.turn_index)
            if len(ordered_turns) >= max_turns:
                break
        allowed = set(ordered_turns)
        return [message for message in transcript if message.turn_index in allowed]

    def _memory_query_text(
        self,
        *,
        transcript: list[ChatMessage],
        working_memory: WorkingMemorySnapshot | None,
    ) -> str:
        parts: list[str] = []
        user_messages = [message.content.strip() for message in transcript if message.message_type == MessageType.USER]
        if user_messages:
            parts.extend(user_messages[-3:])
        if working_memory is not None:
            parts.extend(working_memory.decisions_made[-3:])
            parts.extend(working_memory.open_questions[-3:])
            parts.extend(working_memory.followups[-3:])
            parts.extend(working_memory.active_artifacts[-4:])
            parts.extend(working_memory.evidence_refs[-6:])
        return "\n".join(part for part in parts if part).strip()

    def _bound_paper_ids(self, session: ChatSession) -> list[str]:
        paper_ids: list[str] = []
        for binding in session.context_bindings:
            if binding.provider != "paper_focus":
                continue
            paper_id = str(binding.config.get("paper_id", "")).strip()
            if paper_id and paper_id not in paper_ids:
                paper_ids.append(paper_id)
        return paper_ids

    def _conversation_extra_args(self, provider: ProviderKind) -> list[str]:
        if provider == ProviderKind.CLAUDE:
            return ["--effort", "medium"]
        if provider == ProviderKind.CODEX:
            return ["-c", 'model_reasoning_effort="medium"']
        return []

    def _append_message_event(self, *, session: ChatSession, message: ChatMessage) -> None:
        kind_map = {
            MessageType.USER: SessionEventKind.MESSAGE_USER,
            MessageType.AGENT: SessionEventKind.MESSAGE_AGENT,
            MessageType.SYSTEM: SessionEventKind.MESSAGE_SYSTEM,
        }
        refs: list[str] = []
        if session.project:
            refs.append(f"project:{session.project}")
        self.session_context_store.append_event(
            SessionEvent(
                session_id=session.session_id,
                project=session.project,
                kind=kind_map[message.message_type],
                turn_index=message.turn_index,
                actor=message.speaker,
                summary=self._event_summary_for_message(message),
                payload={
                    "message_id": message.message_id,
                    "provider": message.provider.value if message.provider else None,
                    "reply_to": message.reply_to,
                    "metadata": message.metadata,
                },
                evidence_refs=refs,
            )
        )

    def _event_summary_for_message(self, message: ChatMessage, *, max_chars: int = 280) -> str:
        text = " ".join(message.content.strip().split())
        if len(text) <= max_chars:
            return text
        return f"{text[: max_chars - 1]}…"

    def _promote_event_to_memory(self, event: SessionEvent) -> None:
        try:
            self.memory_service.promote_event(event)
        except Exception:
            return

    def _refresh_working_memory(self, session: ChatSession) -> None:
        events = self.session_context_store.load_events(session.session_id)
        existing = self.session_context_store.load_working_memory(session.session_id)
        updated = self.condenser.condense(
            session_id=session.session_id,
            project=session.project,
            events=events,
            existing=existing,
        )
        self.session_context_store.write_working_memory(updated)
