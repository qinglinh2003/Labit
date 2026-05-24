from __future__ import annotations

import math
import shlex
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Queue

from labit.agents.adapters.base import StreamCancelled
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
    ContextSnapshot,
    MemoryBinding,
    MessageType,
    utc_now_iso,
)
from labit.chat.prompt import ChatContextBuilder
from labit.chat.store import ChatStore
from labit.context.condenser import ResearchRollingCondenser, SessionCondenser
from labit.context.events import SessionEvent, SessionEventKind, WorkingMemorySnapshot
from labit.context.store import SessionContextStore
from labit.models import ComputeProfile
from labit.paths import RepoPaths
from labit.services.project_service import ProjectService


@dataclass
class ChatTurnResult:
    session: ChatSession
    user_message: ChatMessage
    replies: list[ChatReply]
    context_snapshot: ContextSnapshot


@dataclass(frozen=True)
class _TranscriptTurn:
    turn_index: int
    messages: tuple[ChatMessage, ...]


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
    ):
        self.paths = paths
        self.store = store or ChatStore(paths)
        self.registry = registry or ProviderRegistry.default()
        self.context_registry = context_registry or ConversationContextRegistry.default()
        self.project_service = project_service or ProjectService(paths)
        self.session_context_store = session_context_store or SessionContextStore(paths)
        self.condenser = condenser or ResearchRollingCondenser()
        self.prompt_builder = ChatContextBuilder()

    def open_session(
        self,
        *,
        title: str,
        mode: ChatMode,
        provider: str | ProviderKind | None = None,
        second_provider: str | ProviderKind | None = None,
        project: str | None = None,
        memory_bindings: list[MemoryBinding] | None = None,
    ) -> ChatSession:
        session = ChatSession(
            title=title,
            mode=mode,
            project=project,
            participants=self._default_participants(mode=mode, provider=provider, second_provider=second_provider),
            memory_bindings=memory_bindings or [MemoryBinding(provider="session_working_memory")],
        )
        snapshot = self.context_registry.build_snapshot(session=session, transcript=[], paths=self.paths)
        self.store.initialize_session(session, snapshot)
        self.session_context_store.write_working_memory(
            WorkingMemorySnapshot(session_id=session.session_id, project=session.project)
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
        on_reply_status: Callable[[ChatParticipant, str], None] | None = None,
        on_reply_complete: Callable[[ChatParticipant, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        skip_participants: set[str] | None = None,
        cwd_override: str | None = None,
    ) -> ChatTurnResult:
        return self._ask_impl(
            session_id=session_id,
            content=content,
            attachments=attachments,
            force_deep_context=force_deep_context,
            reasoning_effort=reasoning_effort,
            on_reply_start=on_reply_start,
            on_reply_delta=on_reply_delta,
            on_reply_status=on_reply_status,
            on_reply_complete=on_reply_complete,
            cancel_event=cancel_event,
            skip_participants=skip_participants,
            cwd_override=cwd_override,
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
        on_reply_status: Callable[[ChatParticipant, str], None] | None = None,
        on_reply_complete: Callable[[ChatParticipant, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        skip_participants: set[str] | None = None,
        cwd_override: str | None = None,
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

        # Filter out skipped participants for this turn
        effective_participants = [
            p for p in session.participants
            if not skip_participants or p.name not in skip_participants
        ]
        if not effective_participants:
            effective_participants = session.participants

        replies: list[ChatReply] = []
        try:
            if session.mode == ChatMode.PARALLEL:
                reply_queue: Queue[tuple[int, ChatReply | Exception | None]] = Queue()
                threads: list[threading.Thread] = []

                def _parallel_worker(index: int, participant: ChatParticipant) -> None:
                    try:
                        reply = self._generate_reply(
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
                            on_reply_status=on_reply_status,
                            on_reply_complete=on_reply_complete,
                            cancel_event=cancel_event,
                            persist=False,
                            cwd_override=cwd_override,
                        )
                    except (StreamCancelled, KeyboardInterrupt):
                        reply_queue.put((index, None))
                    except Exception as exc:  # noqa: BLE001
                        reply_queue.put((index, exc))
                    else:
                        reply_queue.put((index, reply))

                for index, participant in enumerate(effective_participants):
                    thread = threading.Thread(
                        target=_parallel_worker,
                        args=(index, participant),
                        daemon=True,
                    )
                    thread.start()
                    threads.append(thread)

                ordered_replies: dict[int, ChatReply] = {}
                parallel_errors: list[Exception] = []
                for _ in effective_participants:
                    index, payload = reply_queue.get()
                    if isinstance(payload, ChatReply):
                        ordered_replies[index] = payload
                    elif isinstance(payload, Exception):
                        parallel_errors.append(payload)

                for thread in threads:
                    thread.join()

                replies.extend(ordered_replies[idx] for idx in sorted(ordered_replies))
                for reply in replies:
                    self.store.append_message(reply.message)
                    self._append_message_event(session=session, message=reply.message)

                if parallel_errors and not replies:
                    raise parallel_errors[0]
            else:
                working_transcript = list(base_transcript)
                for participant in effective_participants:
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
                        on_reply_status=on_reply_status,
                        on_reply_complete=on_reply_complete,
                        cancel_event=cancel_event,
                        cwd_override=cwd_override,
                    )
                    replies.append(reply)
                    working_transcript.append(reply.message)
        except (StreamCancelled, KeyboardInterrupt):
            pass  # stop generating, keep any replies already collected

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

    def update_mode(self, session_id: str, mode: ChatMode) -> ChatSession:
        session = self.load_session(session_id)
        updates: dict = {"mode": mode, "updated_at": utc_now_iso()}
        if mode != ChatMode.SINGLE and len(session.participants) < 2:
            existing = session.participants[0] if session.participants else None
            existing_kind = existing.provider if existing else None
            second_kind = self._other_provider(existing_kind) if existing_kind else resolve_provider_kind(None)
            second = ChatParticipant(name=second_kind.value, provider=second_kind)
            updates["participants"] = list(session.participants) + [second]
        updated = session.model_copy(update=updates)
        self.store.write_session(updated)
        return updated

    def swap_participants(self, session_id: str) -> ChatSession:
        """Reverse the order of participants."""
        session = self.load_session(session_id)
        if len(session.participants) < 2:
            raise ValueError("Need at least 2 participants to swap.")
        updated = session.model_copy(update={
            "participants": list(reversed(session.participants)),
            "updated_at": utc_now_iso(),
        })
        self.store.write_session(updated)
        return updated

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
        on_reply_status: Callable[[ChatParticipant, str], None] | None = None,
        on_reply_complete: Callable[[ChatParticipant, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        persist: bool = True,
        cwd_override: str | None = None,
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
            cwd=cwd_override or str(self.paths.root),
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

        def _handle_status(status: str) -> None:
            if on_reply_status is not None:
                on_reply_status(participant, status)

        if on_reply_start is not None:
            on_reply_start(participant)

        if on_reply_delta is not None or on_reply_start is not None or on_reply_complete is not None:
            response = adapter.run_stream(
                request,
                on_text=_handle_delta,
                on_status=_handle_status,
                cancel_event=cancel_event,
            )
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
        if persist:
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
        history_max_turns, history_max_tokens = self._history_budget(
            force_deep_context=force_deep_context,
            has_same_turn_peer=self._same_turn_peer_count(transcript, participant=participant) > 0,
        )
        recent_transcript = self._format_completed_transcript_window(
            transcript,
            max_turns=history_max_turns,
            max_tokens=history_max_tokens,
        )
        peer_input = self._format_same_turn_peer_input(transcript, participant=participant, max_tokens=15000)
        project_label = session.project or "(none)"
        participants = ", ".join(item.name for item in session.participants)
        current_user_message = self._latest_user_message_text(transcript)
        platform_context = self._platform_context(session.project)
        execution_constraints = self._execution_constraints_context(session.project)
        remote_compute_context = self._remote_compute_context(session.project)
        include_prior_state = force_deep_context or self._current_task_requests_prior_state(current_user_message)
        retrieval_reference = self._format_retrieval_reference(
            snapshot=snapshot,
            include_memory_blocks=force_deep_context,
        )

        return self.prompt_builder.build(
            participant_name=participant.name,
            project=project_label,
            mode=session.mode.value,
            participants=participants,
            platform_context=platform_context,
            execution_constraints=execution_constraints,
            remote_compute_context=remote_compute_context,
            current_task=current_user_message,
            prior_state=self._render_compact_working_memory(working_memory),
            history=recent_transcript,
            peer_input=peer_input,
            retrieval_reference=retrieval_reference,
            include_prior_state=include_prior_state,
        )

    def _history_budget(self, *, force_deep_context: bool, has_same_turn_peer: bool) -> tuple[int, int]:
        if force_deep_context:
            return 50, 60000
        if has_same_turn_peer:
            return 12, 12000
        return 20, 20000

    def _current_task_requests_prior_state(self, message: str) -> bool:
        normalized = message.casefold()
        markers = (
            "continue",
            "resume",
            "pick up",
            "where were we",
            "where are we",
            "status",
            "state",
            "todo",
            "open question",
            "decision",
            "working memory",
            "继续",
            "接着",
            "恢复",
            "上次",
            "之前",
            "进度",
            "状态",
            "待办",
            "todo",
            "我们现在写到哪里",
        )
        return any(marker in normalized for marker in markers)

    def _format_retrieval_reference(
        self,
        *,
        snapshot: ContextSnapshot,
        include_memory_blocks: bool,
    ) -> str:
        parts: list[str] = []
        for block in snapshot.blocks[:6]:
            parts.append(f"## {block.title} ({block.source})\n{block.content.strip()}")
        if include_memory_blocks:
            for block in snapshot.memory[:4]:
                parts.append(f"## {block.title} ({block.source})\n{block.content.strip()}")
        return "\n\n".join(part for part in parts if part.strip())

    def _format_transcript_window(self, transcript: list[ChatMessage], *, max_turns: int, max_tokens: int) -> str:
        if not transcript:
            return "(empty conversation)"
        turns = self._group_transcript_turns(transcript)
        return self._render_recent_transcript_turns(turns, max_turns=max_turns, max_tokens=max_tokens)

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

    def _latest_user_message_text(self, transcript: list[ChatMessage]) -> str:
        for message in reversed(transcript):
            if message.message_type == MessageType.USER:
                content = message.content.strip()
                return content or "(empty user message)"
        return "(no user message)"

    def _render_compact_working_memory(self, snapshot: WorkingMemorySnapshot | None) -> str:
        if snapshot is None:
            return "(empty)"
        parts: list[str] = []
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

    def _latest_user_turn_index(self, transcript: list[ChatMessage]) -> int | None:
        for message in reversed(transcript):
            if message.message_type == MessageType.USER:
                return message.turn_index
        return None

    def _format_completed_transcript_window(
        self,
        transcript: list[ChatMessage],
        *,
        max_turns: int,
        max_tokens: int,
    ) -> str:
        current_turn = self._latest_user_turn_index(transcript)
        if current_turn is None:
            return self._format_transcript_window(transcript, max_turns=max_turns, max_tokens=max_tokens)
        completed = [message for message in transcript if message.turn_index < current_turn]
        return self._format_transcript_window(completed, max_turns=max_turns, max_tokens=max_tokens)

    def _group_transcript_turns(self, transcript: list[ChatMessage]) -> list[_TranscriptTurn]:
        grouped: dict[int, list[ChatMessage]] = {}
        for message in transcript:
            grouped.setdefault(message.turn_index, []).append(message)
        return [
            _TranscriptTurn(turn_index=turn_index, messages=tuple(messages))
            for turn_index, messages in sorted(grouped.items(), key=lambda item: item[0])
        ]

    def _render_recent_transcript_turns(
        self,
        turns: list[_TranscriptTurn],
        *,
        max_turns: int,
        max_tokens: int,
    ) -> str:
        if not turns or max_turns <= 0 or max_tokens <= 0:
            return "(empty conversation)"

        candidate_turns = turns[-max_turns:]
        omitted_older_turns = len(turns) - len(candidate_turns)
        selected: list[str] = []
        used_tokens = 0

        for reversed_index, turn in enumerate(reversed(candidate_turns)):
            rendered_turn = self._render_transcript_turn(turn)
            turn_tokens = self._estimate_tokens(rendered_turn)
            if used_tokens + turn_tokens <= max_tokens:
                selected.append(rendered_turn)
                used_tokens += turn_tokens
                continue

            turn_index_in_candidates = len(candidate_turns) - 1 - reversed_index
            if not selected:
                clipped_turn = self._clip_transcript_turn_to_tokens(turn, max_tokens=max_tokens)
                if clipped_turn.strip():
                    selected.append(clipped_turn)
                omitted_older_turns += turn_index_in_candidates
            else:
                omitted_older_turns += turn_index_in_candidates + 1
            break

        selected.reverse()
        if not selected:
            return "(empty conversation)"

        lines: list[str] = []
        if omitted_older_turns:
            noun = "turn" if omitted_older_turns == 1 else "turns"
            lines.append(
                f"[older completed transcript omitted: {omitted_older_turns} {noun} "
                "exceeded history window or budget]"
            )
        lines.extend(selected)
        return "\n\n".join(lines)

    def _render_transcript_turn(self, turn: _TranscriptTurn) -> str:
        rendered_messages = [self._render_transcript_message(message) for message in turn.messages]
        return f"[turn {turn.turn_index}]\n" + "\n\n".join(rendered_messages)

    def _render_transcript_message(self, message: ChatMessage) -> str:
        speaker = self._transcript_speaker_label(message)
        body = message.content
        attachment_text = self._message_attachment_summary(message)
        if attachment_text:
            body = f"{body}\n{attachment_text}"
        return f"{speaker}:\n{body}"

    def _transcript_speaker_label(self, message: ChatMessage) -> str:
        provider = f" ({message.provider.value})" if message.provider else ""
        return f"{message.speaker}{provider}"

    def _clip_transcript_turn_to_tokens(self, turn: _TranscriptTurn, *, max_tokens: int) -> str:
        rendered = self._render_transcript_turn(turn)
        if self._estimate_tokens(rendered) <= max_tokens:
            return rendered

        max_chars = max(80, max_tokens * 4)
        header = f"[turn {turn.turn_index}]"
        if not turn.messages:
            return header

        first_message = turn.messages[0]
        last_message = turn.messages[-1]
        first_rendered = self._render_transcript_message(first_message)
        prefix = f"{header}\n{first_rendered}\n\n" if first_message is not last_message else f"{header}\n"
        omitted_middle_count = max(0, len(turn.messages) - 2)
        middle_marker = (
            f"[{omitted_middle_count} middle messages omitted to fit history budget]\n\n"
            if omitted_middle_count
            else ""
        )
        last_budget = max_chars - len(prefix) - len(middle_marker)
        if last_budget > 120:
            last_rendered = self._clip_transcript_message_to_chars(last_message, max_chars=last_budget)
            candidate = f"{prefix}{middle_marker}{last_rendered}".rstrip()
            if len(candidate) <= max_chars:
                return candidate

        last_rendered = self._clip_transcript_message_to_chars(
            last_message,
            max_chars=max(80, max_chars - len(header) - 1),
        )
        return f"{header}\n{last_rendered}".rstrip()

    def _clip_transcript_message_to_chars(self, message: ChatMessage, *, max_chars: int) -> str:
        rendered = self._render_transcript_message(message)
        if len(rendered) <= max_chars:
            return rendered

        speaker = self._transcript_speaker_label(message)
        body = message.content
        attachment_text = self._message_attachment_summary(message)
        if attachment_text:
            body = f"{body}\n{attachment_text}"

        marker_template = "[message clipped from the beginning: {count} characters omitted]\n"
        fixed = f"{speaker}:\n"
        marker_overhead = len(marker_template.format(count=len(body)))
        body_budget = max(20, max_chars - len(fixed) - marker_overhead)
        tail = body[-body_budget:].lstrip()
        omitted = max(0, len(body) - len(tail))
        marker = marker_template.format(count=omitted)
        clipped = f"{fixed}{marker}{tail}"
        if len(clipped) > max_chars:
            clipped = clipped[-max_chars:].lstrip()
            clipped = f"{fixed}{marker}{clipped[-body_budget:].lstrip()}"
        return clipped

    def _format_same_turn_peer_input(
        self,
        transcript: list[ChatMessage],
        *,
        participant: ChatParticipant,
        max_tokens: int,
    ) -> str:
        current_turn = self._latest_user_turn_index(transcript)
        if current_turn is None:
            return ""
        peer_messages = [
            message
            for message in transcript
            if message.turn_index == current_turn
            and message.message_type == MessageType.AGENT
            and message.speaker != participant.name
        ]
        if not peer_messages:
            return ""
        rendered = "\n\n".join(
            f"[same turn {message.turn_index}] {message.speaker}: {message.content}"
            for message in peer_messages
        )
        return self._clip_to_tokens(rendered, max_tokens=max_tokens)

    def _same_turn_peer_count(self, transcript: list[ChatMessage], *, participant: ChatParticipant) -> int:
        current_turn = self._latest_user_turn_index(transcript)
        if current_turn is None:
            return 0
        return sum(
            1
            for message in transcript
            if message.turn_index == current_turn
            and message.message_type == MessageType.AGENT
            and message.speaker != participant.name
        )

    def _clip_to_tokens(self, text: str, *, max_tokens: int) -> str:
        text = text.strip()
        if self._estimate_tokens(text) <= max_tokens:
            return text
        approx_chars = max(1, max_tokens * 4)
        clipped = text[:approx_chars].rstrip()
        if len(clipped) < len(text):
            clipped = f"{clipped}…"
        return clipped

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, math.ceil(len(text) / 4))

    def _platform_context(self, project: str | None) -> str:
        """Build a static platform-awareness block that agents must always know."""
        lines = [
            "Platform (LABIT):",
            "- LABIT is a lightweight research workspace for projects, documents, and multi-agent discussion.",
            "- The LABIT codebase itself is a separate git repo. Do NOT commit, push, or modify LABIT source code from a project chat.",
        ]
        if project:
            lines.extend([
                f"- You are working on project '{project}'. This project has its own directory under vault/projects/{project}/.",
                f"- You may read LABIT files for reference, but you should only create/modify files within vault/projects/{project}/ and its subdirectories.",
                f"- Git operations (commit, push, branch) should only target the project's files, never LABIT's own source code.",
            ])
            try:
                code_dir = self.project_service.project_code_dir(project)
                if code_dir.exists():
                    lines.append(f"- Project code directory: {code_dir.relative_to(self.paths.root)}")
            except Exception:
                pass
        return "\n".join(lines)

    def _execution_constraints_context(self, project: str | None) -> str:
        if not project:
            return ""
        try:
            spec = self.project_service.load_project(project)
        except Exception:
            return ""
        if not spec.compute_profiles:
            return ""
        return "\n".join(
            [
                "Remote compute constraints:",
                "- Only SSH into a remote machine when the user explicitly asks you to inspect, debug, run, or check something remotely.",
                "- Prefer local project files when the question can be answered locally.",
                "- Treat each profile's workdir as the expected remote project directory.",
                "- When running remote shell commands for a profile with a workdir, start from that workdir using `cd <workdir> && ...` or an equivalent shell command.",
                "- Do not create files in `$HOME`, `/tmp`, or an unspecified remote directory unless the user explicitly asks for that location.",
                "- Before making remote changes, state the intended command or action unless the user already gave a direct instruction.",
                "- Do not run destructive commands, package installs, process killing, or long-running jobs remotely unless explicitly requested.",
                "- When the user asks to update remote code, use rsync from the local project code directory to the profile workdir; do not create git commits just to synchronize files.",
                "- Rsync may include files ignored by git; call out secrets or large local artifacts before syncing when they are likely to matter.",
                "- Do not use rsync `--delete` unless the user explicitly asks for destructive mirroring.",
            ]
        )

    def _remote_compute_context(self, project: str | None) -> str:
        """Build the remote compute affordance block for agent prompts."""
        if not project:
            return ""
        try:
            spec = self.project_service.load_project(project)
        except Exception:
            return ""
        if not spec.compute_profiles:
            return ""
        try:
            local_code_dir = self.project_service.project_code_dir(project)
        except Exception:
            local_code_dir = None

        lines = [
            "Remote Compute Profiles:",
            "This project has SSH access to remote machines. These machines are separate from the local LABIT workspace.",
            "",
            "Profiles:",
        ]
        for profile in spec.compute_profiles:
            lines.append(f"- {profile.name}:")
            lines.append(f"    ssh: {profile.ssh_display()}")
            if profile.workdir:
                lines.append(f"    workdir: {profile.workdir}")
                lines.append(f"    command pattern: {profile.ssh_display()} \"cd {self._remote_cd_path(profile.workdir)} && <command>\"")
                if local_code_dir is not None:
                    lines.append(f"    sync pattern: {self._rsync_display(profile, local_code_dir)}")
            else:
                lines.append("    workdir: (not configured; ask the user before creating or editing remote files)")
            if profile.notes:
                lines.append(f"    notes: {profile.notes}")
        return "\n".join(lines)

    def _remote_cd_path(self, path: str) -> str:
        path = path.strip()
        if path == "~":
            return "$HOME"
        if path.startswith("~/"):
            return f"$HOME/{shlex.quote(path[2:])}"
        return shlex.quote(path)

    def _rsync_display(self, profile: ComputeProfile, local_code_dir: Path) -> str:
        ssh_parts = ["ssh"]
        if profile.connection.identity_file:
            ssh_parts.extend(["-i", str(Path(profile.connection.identity_file).expanduser())])
        if profile.connection.port != 22:
            ssh_parts.extend(["-p", str(profile.connection.port)])

        remote_path = profile.workdir.rstrip("/") + "/"
        command = [
            "rsync",
            "-az",
            "--exclude",
            ".git/",
            "--exclude",
            "__pycache__/",
            "--exclude",
            ".venv/",
            "--exclude",
            "venv/",
            "--exclude",
            ".pytest_cache/",
            "-e",
            shlex.join(ssh_parts),
            f"{local_code_dir}/",
            f"{profile.connection.target}:{remote_path}",
        ]
        return shlex.join(command)

    def _conversation_extra_args(self, provider: ProviderKind, *, reasoning_effort: str) -> list[str]:
        if provider == ProviderKind.CLAUDE:
            return [
                "--effort",
                self._claude_effort(reasoning_effort),
                "--disable-slash-commands",
                "--no-session-persistence",
                "--tools",
                "Read,LS,Glob,Grep,Edit,Write,Bash,WebFetch,WebSearch",
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
