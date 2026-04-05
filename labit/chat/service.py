from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from labit.agents.models import AgentRequest, ProviderKind
from labit.agents.orchestrator import ProviderRegistry
from labit.agents.providers import discussion_provider_kinds, provider_available, resolve_provider_kind
from labit.chat.context import ConversationContextRegistry
from labit.chat.models import (
    ChatAttachment,
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
    DEFAULT_REASONING_EFFORT = "medium"
    THINK_REASONING_EFFORT = "high"

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

    def ask(
        self,
        *,
        session_id: str,
        content: str,
        attachments: list[ChatAttachment] | None = None,
    ) -> ChatTurnResult:
        return self._ask_impl(session_id=session_id, content=content, attachments=attachments)

    def ask_stream(
        self,
        *,
        session_id: str,
        content: str,
        attachments: list[ChatAttachment] | None = None,
        force_deep_context: bool = False,
        reasoning_effort: str | None = None,
        on_reply_start: Callable[[ChatParticipant], None] | None = None,
        on_reply_delta: Callable[[ChatParticipant, str], None] | None = None,
        on_reply_complete: Callable[[ChatParticipant, str], None] | None = None,
    ) -> ChatTurnResult:
        return self._ask_impl(
            session_id=session_id,
            content=content,
            attachments=attachments,
            force_deep_context=force_deep_context,
            reasoning_effort=reasoning_effort,
            on_reply_start=on_reply_start,
            on_reply_delta=on_reply_delta,
            on_reply_complete=on_reply_complete,
        )

    def _ask_impl(
        self,
        *,
        session_id: str,
        content: str,
        attachments: list[ChatAttachment] | None = None,
        force_deep_context: bool = False,
        reasoning_effort: str | None = None,
        on_reply_start: Callable[[ChatParticipant], None] | None = None,
        on_reply_delta: Callable[[ChatParticipant, str], None] | None = None,
        on_reply_complete: Callable[[ChatParticipant, str], None] | None = None,
    ) -> ChatTurnResult:
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
            attachments=attachments or [],
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
                        force_deep_context=force_deep_context,
                        reasoning_effort=reasoning_effort,
                        on_reply_start=on_reply_start,
                        on_reply_delta=on_reply_delta,
                        on_reply_complete=on_reply_complete,
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
                    force_deep_context=force_deep_context,
                    reasoning_effort=reasoning_effort,
                    on_reply_start=on_reply_start,
                    on_reply_delta=on_reply_delta,
                    on_reply_complete=on_reply_complete,
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
        force_deep_context: bool = False,
        reasoning_effort: str | None = None,
        on_reply_start: Callable[[ChatParticipant], None] | None = None,
        on_reply_delta: Callable[[ChatParticipant, str], None] | None = None,
        on_reply_complete: Callable[[ChatParticipant, str], None] | None = None,
    ) -> ChatReply:
        adapter = self.registry.get(participant.provider)
        request = AgentRequest(
            role=self._participant_role(session.mode),
            prompt=self._build_prompt(
                session=session,
                participant=participant,
                transcript=transcript,
                snapshot=snapshot,
                force_deep_context=force_deep_context,
            ),
            cwd=str(self.paths.root),
            timeout_seconds=120,
            image_paths=self._recent_image_paths(transcript),
            extra_args=self._conversation_extra_args(
                participant.provider,
                reasoning_effort=reasoning_effort or self.DEFAULT_REASONING_EFFORT,
            ),
        )
        accumulated = ""

        def _handle_delta(chunk: str) -> None:
            nonlocal accumulated
            accumulated += chunk
            if on_reply_delta is not None:
                on_reply_delta(participant, accumulated)

        if on_reply_start is not None:
            on_reply_start(participant)

        if on_reply_delta is not None or on_reply_start is not None or on_reply_complete is not None:
            response = adapter.run_stream(request, on_text=_handle_delta)
        else:
            response = adapter.run(request)

        final_content = response.raw_output.strip()
        if on_reply_complete is not None:
            on_reply_complete(participant, final_content)
        message = ChatMessage(
            session_id=session.session_id,
            turn_index=turn_index,
            message_type=MessageType.AGENT,
            speaker=participant.name,
            provider=participant.provider,
            content=final_content,
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
        force_deep_context: bool = False,
    ) -> str:
        working_memory = self.session_context_store.load_working_memory(session.session_id)
        if self._use_compact_chat_prompt(session=session) and not force_deep_context:
            return self._build_compact_chat_prompt(
                session=session,
                participant=participant,
                transcript=transcript,
                working_memory=working_memory,
            )

        participants = ", ".join(item.name for item in session.participants)
        assembled_context = self._assemble_context(
            session=session,
            transcript=transcript,
            snapshot=snapshot,
            deep_memory=force_deep_context,
        )

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
        deep_memory: bool = False,
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
            allow_fallback=deep_memory,
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
                limit=12 if deep_memory else 6,
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
        return self._format_transcript_window(transcript, max_turns=50, max_tokens=60000)

    def _format_transcript_window(self, transcript: list[ChatMessage], *, max_turns: int, max_tokens: int) -> str:
        if not transcript:
            return "(empty conversation)"
        recent = self._recent_turn_window(transcript, max_turns=max_turns)
        lines: list[str] = []
        for message in recent:
            provider = f" ({message.provider.value})" if message.provider else ""
            attachment_text = self._message_attachment_summary(message)
            line = f"[turn {message.turn_index}] {message.speaker}{provider}: {message.content}"
            if attachment_text:
                line = f"{line}\n{attachment_text}"
            lines.append(line)
        rendered = "\n\n".join(lines)
        return self.assembler.clip_to_tokens(rendered, max_tokens=max_tokens)

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

    def _recent_image_paths(self, transcript: list[ChatMessage], *, max_images: int = 4) -> list[str]:
        image_paths: list[str] = []
        recent = self._recent_turn_window(transcript, max_turns=50)
        for message in reversed(recent):
            for attachment in reversed(message.attachments):
                if attachment.kind.value != "image":
                    continue
                if attachment.path in image_paths:
                    continue
                image_paths.append(attachment.path)
                if len(image_paths) >= max_images:
                    return list(reversed(image_paths))
        return list(reversed(image_paths))

    def _message_attachment_summary(self, message: ChatMessage) -> str:
        if not message.attachments:
            return ""
        lines: list[str] = []
        for attachment in message.attachments:
            label = attachment.label or attachment.path.rsplit("/", 1)[-1]
            lines.append(f"  [attached {attachment.kind.value}] {label} @ {attachment.path}")
        return "\n".join(lines)

    def _use_lightweight_prompt(
        self,
        *,
        session: ChatSession,
        snapshot: ContextSnapshot,
        working_memory: WorkingMemorySnapshot | None,
    ) -> bool:
        return self._use_compact_chat_prompt(session=session)

    def _use_compact_chat_prompt(self, *, session: ChatSession) -> bool:
        return not any(binding.provider != "none" for binding in session.context_bindings)

    def _build_compact_chat_prompt(
        self,
        *,
        session: ChatSession,
        participant: ChatParticipant,
        transcript: list[ChatMessage],
        working_memory: WorkingMemorySnapshot | None,
    ) -> str:
        recent_transcript = self._format_transcript_window(transcript, max_turns=50, max_tokens=60000)
        project_label = session.project or "(none)"
        participants = ", ".join(item.name for item in session.participants)
        working_memory_text = self._render_compact_working_memory(working_memory)
        return f"""You are `{participant.name}` in a LABIT research conversation.

Project: {project_label}
Mode: {session.mode.value}
Participants: {participants}

Guidelines:
- Continue the conversation naturally.
- Use the recent transcript and working memory as the shared state.
- Distinguish evidence from inference when it matters.
- Be concise and specific.

Working memory:
{working_memory_text}

Recent transcript:
{recent_transcript}

Reply as `{participant.name}` only. Use plain text or markdown.
"""

    def _render_compact_working_memory(self, snapshot: WorkingMemorySnapshot | None) -> str:
        if snapshot is None:
            return "(empty)"
        parts: list[str] = []
        if snapshot.current_goal:
            parts.append(f"Current goal: {snapshot.current_goal}")
        if snapshot.active_artifacts:
            parts.append(f"Active artifacts: {', '.join(snapshot.active_artifacts)}")
        if snapshot.decisions_made:
            parts.append("Decisions:")
            parts.extend(f"- {item}" for item in snapshot.decisions_made[-4:])
        if snapshot.open_questions:
            parts.append("Open questions:")
            parts.extend(f"- {item}" for item in snapshot.open_questions[-4:])
        if snapshot.discussion_state.consensus:
            parts.append("Consensus:")
            parts.extend(f"- {item}" for item in snapshot.discussion_state.consensus[-3:])
        if snapshot.discussion_state.disagreements:
            parts.append("Disagreements:")
            parts.extend(f"- {item}" for item in snapshot.discussion_state.disagreements[-3:])
        if snapshot.followups:
            parts.append("Follow-ups:")
            parts.extend(f"- {item}" for item in snapshot.followups[-4:])
        if snapshot.evidence_refs:
            meaningful_refs = [ref for ref in snapshot.evidence_refs if not ref.startswith("project:")]
            if meaningful_refs:
                parts.append("Evidence refs:")
                parts.extend(f"- {item}" for item in meaningful_refs[-6:])
        return "\n".join(parts) if parts else "(empty)"

    def _bound_paper_ids(self, session: ChatSession) -> list[str]:
        paper_ids: list[str] = []
        for binding in session.context_bindings:
            if binding.provider != "paper_focus":
                continue
            paper_id = str(binding.config.get("paper_id", "")).strip()
            if paper_id and paper_id not in paper_ids:
                paper_ids.append(paper_id)
        return paper_ids

    def _conversation_extra_args(self, provider: ProviderKind, *, reasoning_effort: str) -> list[str]:
        if provider == ProviderKind.CLAUDE:
            return [
                "--effort",
                self._claude_effort(reasoning_effort),
                "--disable-slash-commands",
                "--no-session-persistence",
                "--tools",
                "Read,LS,Glob,Grep,Edit,Write",
                "--permission-mode",
                "bypassPermissions",
            ]
        if provider == ProviderKind.CODEX:
            return ["-c", f'model_reasoning_effort="{self._codex_effort(reasoning_effort)}"']
        return []

    def _claude_effort(self, effort: str) -> str:
        normalized = effort.strip().lower()
        if normalized in {"low", "medium", "high"}:
            return normalized
        return self.DEFAULT_REASONING_EFFORT

    def _codex_effort(self, effort: str) -> str:
        normalized = effort.strip().lower()
        if normalized == "high":
            return "high"
        if normalized == "low":
            return "low"
        return "medium"

    def _append_message_event(self, *, session: ChatSession, message: ChatMessage) -> None:
        kind_map = {
            MessageType.USER: SessionEventKind.MESSAGE_USER,
            MessageType.AGENT: SessionEventKind.MESSAGE_AGENT,
            MessageType.SYSTEM: SessionEventKind.MESSAGE_SYSTEM,
        }
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
                    "attachments": [attachment.model_dump(mode="json") for attachment in message.attachments],
                    "metadata": message.metadata,
                },
                evidence_refs=[],
            )
        )

    def _event_summary_for_message(self, message: ChatMessage, *, max_chars: int = 280) -> str:
        text = " ".join(message.content.strip().split())
        if message.attachments:
            attachment_bits = ", ".join(
                f"{attachment.kind.value}:{attachment.label or attachment.path.rsplit('/', 1)[-1]}"
                for attachment in message.attachments
            )
            text = f"{text} [attachments: {attachment_bits}]".strip()
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
